"""Devshell 评测：自动打分（``score_devshell_tasks --submit``）完成后的飞书群通知。

Webhook 使用仓库根 ``utils.feishu_webhook.FEISHU_WEBHOOK_URL``，与 API / Worker 一致。

发送在后台线程执行，失败只打 log，不抛异常。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from utils.feishu_webhook import FEISHU_WEBHOOK_URL

logger = logging.getLogger(__name__)

# 与 src.utils.feishu_notifier 一致的重试策略
_SEND_MAX_RETRIES = 3
_SEND_RETRY_DELAYS = (1, 2, 3)

_MAX_REASON_INLINE = 420
_MAX_BODY_CHARS = 11_000


def _post_webhook(url: str, body: dict[str, Any]) -> None:
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(_SEND_MAX_RETRIES):
        try:
            with urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    preview = resp.read()[:240]
                    logger.warning(
                        "Devshell eval Feishu HTTP status=%s body=%s",
                        resp.status,
                        preview,
                    )
                return
        except HTTPError as e:
            if e.code is None or e.code < 500 or e.code > 599:
                logger.warning("Devshell eval Feishu failed: %s", e)
                return
            if attempt < _SEND_MAX_RETRIES - 1:
                time.sleep(_SEND_RETRY_DELAYS[attempt])
            else:
                logger.warning(
                    "Devshell eval Feishu failed after %s attempts: %s",
                    _SEND_MAX_RETRIES,
                    e,
                )
        except (URLError, OSError) as e:
            if attempt < _SEND_MAX_RETRIES - 1:
                time.sleep(_SEND_RETRY_DELAYS[attempt])
            else:
                logger.warning(
                    "Devshell eval Feishu failed after %s attempts: %s",
                    _SEND_MAX_RETRIES,
                    e,
                )


def _load_pending_rows(pending_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(pending_dir.glob("*.json")):
        try:
            envelope = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("skip pending %s: %s", p, e)
            continue
        item = envelope.get("item")
        if not isinstance(item, dict):
            item = {}
        tid = envelope.get("task_id") or p.stem
        qid = item.get("question_id") or tid
        score = item.get("score")
        reason = item.get("score_reason") or ""
        if isinstance(reason, str) and len(reason) > _MAX_REASON_INLINE:
            reason = reason[:_MAX_REASON_INLINE] + "…"
        rows.append(
            {
                "task_id": str(tid),
                "question_id": str(qid),
                "score": score if isinstance(score, int) else None,
                "score_reason": reason if isinstance(reason, str) else "",
            }
        )
    return rows


def _macro_mean(scores: list[int | None]) -> float | None:
    vals = [s for s in scores if isinstance(s, int)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _build_markdown_body(
    *,
    tag: str,
    run_dir: Path,
    rows: list[dict[str, Any]],
    submit_ok: bool,
    stderr_tail: str,
) -> tuple[str, str]:
    """Returns (template_color, markdown_content)."""
    n = len(rows)
    scores = [r["score"] for r in rows]
    mm = _macro_mean(scores)
    mm_s = f"{mm:.2f}" if mm is not None else "—"

    lines: list[str] = [
        f"**目录 tag**\n`{tag}`",
        f"**run_dir**\n`{run_dir}`",
        f"**题目数**\n{n}",
        f"**宏平均 (0–100)**\n{mm_s}",
        "",
        "**各题得分**",
    ]
    for r in rows:
        sid = r["question_id"]
        sc = r["score"]
        sc_s = str(sc) if isinstance(sc, int) else "未写入"
        lines.append(f"- `{sid}`：**{sc_s}**")

    missing = sum(1 for s in scores if not isinstance(s, int))
    if missing:
        lines.extend(
            ["", f"（{missing} 题 pending 中无 `item.score`，可能判分未完成）"]
        )

    lines.extend(["", "**判分说明（节选）**"])
    shown = 0
    for r in rows:
        reason = (r.get("score_reason") or "").strip()
        if not reason:
            continue
        lines.append(f"- `{r['question_id']}`：{reason}")
        shown += 1
        if shown >= 24:
            lines.append("- …（判分说明过多已省略，见 `pending_ingest/`）")
            break

    if submit_ok and not any((r.get("score_reason") or "").strip() for r in rows):
        lines.append("- （无 score_reason 文本）")

    if not submit_ok and stderr_tail.strip():
        tail = stderr_tail.strip()
        if len(tail) > 3500:
            tail = tail[:3500] + "…"
        tail = tail.replace("```", "'''")
        lines.extend(["", "**score 子进程 stderr（节选）**", f"```\n{tail}\n```"])

    body = "\n".join(lines)
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n\n…（正文过长已截断）"

    if not submit_ok:
        return "red", body
    if missing:
        return "orange", body
    return "green", body


def _send_interactive_card(
    *,
    webhook: str,
    title: str,
    markdown: str,
    template: str,
) -> None:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": markdown}},
    ]
    body = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title[:200]},
                "template": template,
            },
            "elements": elements,
        },
    }
    _post_webhook(webhook, body)


def _notify_impl(
    *,
    webhook: str,
    run_dir: Path,
    ingest_result: dict[str, Any],
) -> None:
    try:
        tag = run_dir.name
        attempted = bool(ingest_result.get("attempted"))
        ok = bool(ingest_result.get("ok"))
        stderr_tail = str(ingest_result.get("stderr_tail") or "")

        if not attempted:
            return

        pending_dir = run_dir / "pending_ingest"
        rows = _load_pending_rows(pending_dir) if pending_dir.is_dir() else []

        env = (os.environ.get("SERVICE_ENV") or "").strip()
        env_prefix = f"[{env}] " if env else ""
        title = f"{env_prefix}Devshell 评测打分 · {tag}" + (" ✓" if ok else " ✗")
        md_template, md = _build_markdown_body(
            tag=tag,
            run_dir=run_dir.resolve(),
            rows=rows,
            submit_ok=ok,
            stderr_tail=stderr_tail,
        )
        _send_interactive_card(
            webhook=webhook,
            title=title,
            markdown=md,
            template=md_template,
        )
    except Exception:
        logger.exception("Devshell eval Feishu notify failed run_dir=%s", run_dir)


def notify_after_scoring_async(
    *,
    run_dir: Path,
    ingest_result: dict[str, Any],
) -> None:
    """在后台线程向 ``FEISHU_WEBHOOK_URL`` 发送打分结果卡片。"""
    if not ingest_result.get("attempted"):
        return

    t = threading.Thread(
        target=_notify_impl,
        kwargs={
            "webhook": FEISHU_WEBHOOK_URL,
            "run_dir": run_dir,
            "ingest_result": ingest_result,
        },
        name="devshell_eval_feishu",
        daemon=True,
    )
    t.start()
