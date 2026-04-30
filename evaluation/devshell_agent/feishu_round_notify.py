"""Devshell 评测：飞书群通知。

- 自动打分（``score_devshell_tasks --submit``）完成后的结果卡片。
- Checklist / 优化子回合写出 **待人工合入** 的 ``proposed_*.md`` 时的提醒卡片。

打分卡片中，同一 ``question_id`` 的多次 repeat 会合并为 **一道题**：仅当该题
**全部** repeat 均为 ``score==100`` 时记为「通过」，否则为「不通过」；若任一 repeat
尚无整数 ``score``，该题记为「未写入」。

Webhook 使用仓库根 ``utils.feishu_webhook.FEISHU_WEBHOOK_URL``，与 API / Worker 一致。

发送在后台线程执行，失败只打 log，不抛异常。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from utils.feishu_webhook import FEISHU_WEBHOOK_URL

logger = logging.getLogger(__name__)

# 与 src.utils.feishu_notifier 一致的重试策略
_SEND_MAX_RETRIES = 3
_SEND_RETRY_DELAYS = (1, 2, 3)

_MAX_REASON_INLINE = 420
_MAX_BODY_CHARS = 11_000
_MAX_REPORT_INLINE = 900
_MAX_PROPOSAL_PREVIEW = 2800


ProposalKind = Literal["question_bank", "matmaster_exps", "optimization"]


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


def _aggregate_by_question_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将同一题的多次 repeat 合并为一行，用于飞书卡片统计与列表。

    - 通过：该 ``question_id`` 下每条 repeat 均有整数 ``score`` 且均为 ``100``。
    - 不通过：存在整数 ``score`` 且并非全部为 ``100``（含 ``0`` 或混有非 ``100``）。
    - 未写入：任一 repeat 缺少整数 ``score``。
    """
    by_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        qid = str(r.get("question_id") or r.get("task_id") or "").strip()
        if not qid:
            continue
        by_q[qid].append(r)

    out: list[dict[str, Any]] = []
    for qid in sorted(by_q.keys()):
        group = by_q[qid]
        scores = [g.get("score") for g in group]
        if any(not isinstance(s, int) for s in scores):
            agg_score = None
        elif all(s == 100 for s in scores):
            agg_score = 100
        else:
            agg_score = 0
        tid0 = group[0].get("task_id") or ""
        out.append(
            {
                "task_id": str(tid0),
                "question_id": qid,
                "score": agg_score,
                "score_reason": "",
            }
        )
    return out


def _rows_sorted_by_score_desc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feishu「各题判定」：优先展示「通过」；无有效 ``item.score`` 的排在末尾。"""
    return sorted(
        rows,
        key=lambda r: (
            0 if isinstance(r.get("score"), int) else 1,
            -(r["score"] if isinstance(r.get("score"), int) else 0),
            r["question_id"],
        ),
    )


def _devshell_score_label(score: int | None) -> str:
    """Devshell 判分为 0/100 全项通过制：展示「通过 / 不通过」，避免出现裸 ``100``。"""
    if not isinstance(score, int):
        return "未写入"
    if score == 100:
        return "通过"
    if score == 0:
        return "不通过"
    return str(score)


def _full_pass_summary_line(scores: list[int | None]) -> str:
    """汇总行：``n/m 题全项通过``（仅 ``score==100`` 计为通过），不用 0–100 均值数字。"""
    vals = [s for s in scores if isinstance(s, int)]
    if not vals:
        return "—"
    n_pass = sum(1 for s in vals if s == 100)
    return f"{n_pass}/{len(vals)} 题全项通过"


def _build_scoring_notify_title(
    *,
    env_prefix: str,
    tag: str,
    rows: list[dict[str, Any]],
    submit_ok: bool,
) -> str:
    """卡片标题用语义化通过情况，不写分数。"""
    rows = _aggregate_by_question_id(rows)
    base = f"{env_prefix}Devshell 评测打分 · {tag}"
    if not submit_ok:
        return f"{base} · 上报失败"[:200]
    scores = [r["score"] for r in rows]
    if any(not isinstance(s, int) for s in scores):
        return f"{base} · 判分未完成"[:200]
    vals = [s for s in scores if isinstance(s, int)]
    if not vals:
        return f"{base} · 无有效题目"[:200]
    n_pass = sum(1 for s in vals if s == 100)
    if n_pass == len(vals):
        return f"{base} · 全部通过"[:200]
    return f"{base} · 未全部通过"[:200]


def _build_markdown_body(
    *,
    tag: str,
    run_dir: Path,
    rows: list[dict[str, Any]],
    submit_ok: bool,
    stderr_tail: str,
) -> tuple[str, str]:
    """Returns (template_color, markdown_content)."""
    rows = _aggregate_by_question_id(rows)
    n = len(rows)
    scores = [r["score"] for r in rows]
    summary = _full_pass_summary_line(scores)

    lines: list[str] = [
        f"**目录 tag**\n`{tag}`",
        f"**run_dir**\n`{run_dir}`",
        f"**题目数**\n{n}",
        f"**全项通过情况**\n{summary}",
        "",
        "**各题判定**",
    ]
    for r in _rows_sorted_by_score_desc(rows):
        sid = r["question_id"]
        sc = r["score"]
        sc_s = _devshell_score_label(sc)
        lines.append(f"- `{sid}`：**{sc_s}**")

    missing = sum(1 for s in scores if not isinstance(s, int))
    if missing:
        lines.extend(
            ["", f"（{missing} 题 pending 中无 `item.score`，可能判分未完成）"]
        )

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
        title = _build_scoring_notify_title(
            env_prefix=env_prefix, tag=tag, rows=rows, submit_ok=ok
        )
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


def _read_proposal_preview(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    raw = raw.strip()
    if len(raw) > _MAX_PROPOSAL_PREVIEW:
        raw = raw[:_MAX_PROPOSAL_PREVIEW] + "…"
    return raw.replace("```", "'''")


def _notify_manual_review_proposal_impl(
    *,
    webhook: str,
    kind: ProposalKind,
    session_dir: Path,
    iteration_index: int,
    proposal_path: Path,
    report_text: str,
    optimization_round: int | None,
) -> None:
    try:
        if not proposal_path.is_file():
            return
        size = proposal_path.stat().st_size
        if size <= 0:
            return

        env = (os.environ.get("SERVICE_ENV") or "").strip()
        env_prefix = f"[{env}] " if env else ""
        rnd_s = (
            f" · opt_round {optimization_round}"
            if optimization_round is not None
            else ""
        )
        if kind == "question_bank":
            title = (
                f"{env_prefix}Devshell · 题库提案（待合入） · iter {iteration_index}"
            )
        elif kind == "optimization":
            title = (
                f"{env_prefix}Devshell · 优化提案（待合入） · "
                f"iter {iteration_index}{rnd_s}"
            )
        else:
            title = (
                f"{env_prefix}Devshell · exp/系统提示词提案（待合入） · "
                f"iter {iteration_index}{rnd_s}"
            )

        rt = (report_text or "").strip()
        if len(rt) > _MAX_REPORT_INLINE:
            rt = rt[:_MAX_REPORT_INLINE] + "…"

        preview = _read_proposal_preview(proposal_path)
        lines: list[str] = [
            f"**类型**\n{'题库 / checklist' if kind == 'question_bank' else '产品侧优化提案（skill / tool / prompt）' if kind == 'optimization' else 'matmaster/exps（系统提示词层）'}",
            f"**迭代**\n{iteration_index}",
            f"**会话目录**\n`{session_dir.resolve()}`",
            f"**提案文件**\n`{proposal_path.name}`（{size} bytes）",
        ]
        if optimization_round is not None and kind in ("matmaster_exps", "optimization"):
            lines.append(f"**optimization_round**\n{optimization_round}")
        if rt:
            lines.extend(["", "**子 Agent 报告摘要**", rt])
        if preview:
            lines.extend(["", "**正文预览**", f"```\n{preview}\n```"])

        body = "\n".join(lines)
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "\n\n…（正文过长已截断）"

        _send_interactive_card(
            webhook=webhook,
            title=title,
            markdown=body,
            template="orange",
        )
    except Exception:
        logger.exception(
            "Devshell manual-review proposal Feishu notify failed kind=%s path=%s",
            kind,
            proposal_path,
        )


def notify_manual_review_proposal_async(
    *,
    kind: ProposalKind,
    session_dir: Path,
    iteration_index: int,
    proposal_path: Path,
    report_text: str = "",
    optimization_round: int | None = None,
) -> None:
    """子回合写出非空 proposal 文件时发飞书提醒。"""
    if not (FEISHU_WEBHOOK_URL or "").strip():
        return
    if not proposal_path.is_file() or proposal_path.stat().st_size <= 0:
        return

    t = threading.Thread(
        target=_notify_manual_review_proposal_impl,
        kwargs={
            "webhook": FEISHU_WEBHOOK_URL,
            "kind": kind,
            "session_dir": session_dir,
            "iteration_index": iteration_index,
            "proposal_path": proposal_path,
            "report_text": report_text,
            "optimization_round": optimization_round,
        },
        name="devshell_proposal_feishu",
        daemon=True,
    )
    t.start()
