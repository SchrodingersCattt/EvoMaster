"""飞书事件订阅 HTTP 回调：解析、幂等、入队对话并最终回复飞书。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from src.dao.feishu_app_config_table import FeishuAppConfig, get_feishu_app_config_table
from src.dao.feishu_binding_table import get_feishu_binding_table
from src.dao.redis_dao import get_redis_dao
from src.models.chat import ChatSendRequest
from src.services.feishu_open_api import (
    add_reaction,
    get_tenant_access_token,
    remove_reaction,
    reply_text_message,
)
from src.services.quota_service import check_quota
from src.services.stream_service import get_stream_service
from src.utils.constant import REDIS_URL
from src.utils.feishu_event_crypto import parse_event_json as feishu_parse_event_json
from src.utils.feishu_event_crypto import (
    verify_lark_signature,
)

logger = logging.getLogger(__name__)

_FEISHU_DEDUP_PREFIX = "feishu:evt:"
_FEISHU_SESS_PREFIX = "feishu:sess:"
_MAX_REPLY_LEN = 12000


def _mention_pattern() -> re.Pattern[str]:
    return re.compile(r"@_user_\d+\s*")


def _extract_text_from_message(message: dict[str, Any]) -> str:
    raw = message.get("content") or ""
    if not isinstance(raw, str):
        return ""
    try:
        j = json.loads(raw)
        if isinstance(j, dict) and "text" in j:
            t = str(j.get("text") or "")
            t = _mention_pattern().sub("", t).strip()
            return t
    except json.JSONDecodeError:
        pass
    return raw.strip()


def _collect_current_response_chunks(
    sse_piece: str,
    invocation_id: str,
) -> list[str]:
    """只收集当前 invocation 的 MatMaster response 片段，忽略历史重放。"""
    chunks: list[str] = []
    for line in sse_piece.splitlines():
        ls = line.strip()
        if not ls.startswith("data:"):
            continue
        try:
            payload = json.loads(ls[5:].strip())
        except json.JSONDecodeError:
            continue
        if payload.get("invocation_id") != invocation_id:
            continue
        if payload.get("type") == "response" and payload.get("source") == "MatMaster":
            content = payload.get("content")
            if isinstance(content, str) and content:
                chunks.append(content)
        elif payload.get("type") == "error":
            err = payload.get("content")
            if isinstance(err, str) and err:
                chunks.append(f"[错误] {err}")
    return chunks


async def _run_agent_and_reply_feishu(
    *,
    user_id: str,
    session_id: str,
    user_prompt: str,
    message_id: str,
    tenant_token: str,
) -> None:
    stream_svc = get_stream_service()
    req = ChatSendRequest(content=user_prompt, mode="direct")
    try:
        remaining = await check_quota(user_id)
        if remaining <= 0:
            reply_text_message(
                message_id,
                "当日免费额度已用完，请稍后再试或通过网页申请额度。",
                tenant_token=tenant_token,
            )
            return
    except Exception as e:
        logger.warning("feishu check_quota failed user_id=%s: %s", user_id, e)
        reply_text_message(
            message_id,
            "无法校验配额，请稍后重试。",
            tenant_token=tenant_token,
        )
        return

    if not REDIS_URL:
        reply_text_message(
            message_id,
            "服务未配置任务队列（REDIS_URL），无法执行对话。",
            tenant_token=tenant_token,
        )
        return

    try:
        ctx = stream_svc.prepare_send_message(session_id, req, user_id, org_id=None)
    except Exception:
        logger.exception("feishu prepare_send_message failed session_id=%s", session_id)
        reply_text_message(
            message_id,
            "准备对话失败，请稍后重试。",
            tenant_token=tenant_token,
        )
        return

    if ctx is None:
        reply_text_message(
            message_id,
            "当前会话已有任务在执行，请等待完成后再发消息。",
            tenant_token=tenant_token,
        )
        return

    reaction_id = add_reaction(message_id, "OnIt", tenant_token=tenant_token)

    base_prompt = user_prompt.strip()
    parts: list[str] = []
    try:
        async for sse_piece in stream_svc.generate_send_stream(
            session_id, base_prompt, ctx
        ):
            parts.extend(_collect_current_response_chunks(sse_piece, ctx.invocation_id))
    except Exception:
        logger.exception("feishu generate_send_stream failed session_id=%s", session_id)
        reply_text_message(
            message_id,
            "执行过程出错，请稍后重试。",
            tenant_token=tenant_token,
        )
        return
    finally:
        if reaction_id:
            remove_reaction(message_id, reaction_id, tenant_token=tenant_token)

    text = "".join(parts).strip()
    if len(text) > _MAX_REPLY_LEN:
        text = text[:_MAX_REPLY_LEN] + "\n…（已截断）"
    if not text:
        text = "（本轮未产生可见回复，请查看网页端会话详情）"

    try:
        reply_text_message(message_id, text, tenant_token=tenant_token)
    except Exception:
        logger.exception("feishu reply_text_message failed message_id=%s", message_id)


def _session_id_for_chat(chat_id: str, open_id: str) -> str:
    if not REDIS_URL:
        return f"feishu_{uuid.uuid4().hex}"
    client = get_redis_dao().create_client()
    if not client:
        return f"feishu_{uuid.uuid4().hex}"
    key = f"{_FEISHU_SESS_PREFIX}{chat_id}:{open_id}"
    try:
        existing = client.get(key)
        if existing:
            return str(existing)
        sid = f"feishu_{uuid.uuid4().hex}"
        client.set(key, sid)
        return sid
    except Exception as e:
        logger.warning("feishu session redis failed: %s", e)
        return f"feishu_{uuid.uuid4().hex}"


def _event_dedup_once(message_id: str) -> bool:
    if not REDIS_URL or not message_id:
        return True
    client = get_redis_dao().create_client()
    if not client:
        return True
    key = _FEISHU_DEDUP_PREFIX + message_id
    try:
        ok = client.set(key, "1", ex=86400, nx=True)
        return bool(ok)
    except Exception as e:
        logger.warning("feishu dedup redis failed: %s", e)
        return True


async def handle_feishu_im_message_event_v1(
    event_obj: dict[str, Any],
    app_cfg: FeishuAppConfig,
) -> None:
    """处理解密后的 im.message.receive_v1（event 字段内容）。"""
    sender = event_obj.get("sender") or {}
    sender_type = (sender.get("sender_type") or "").strip().lower()
    if sender_type == "bot":
        return

    sender_id = sender.get("sender_id") or {}
    open_id = (sender_id.get("open_id") or "").strip()
    if not open_id:
        logger.warning("feishu event missing open_id")
        return

    message = event_obj.get("message") or {}
    message_id = (message.get("message_id") or "").strip()
    chat_id = (message.get("chat_id") or "").strip()

    if not _event_dedup_once(message_id):
        logger.info("feishu duplicate message_id=%s skip", message_id)
        return

    user_prompt = _extract_text_from_message(message)
    if not user_prompt:
        logger.info("feishu empty text after extract message_id=%s", message_id)
        return

    tenant_token = get_tenant_access_token(app_cfg.app_id, app_cfg.app_secret)

    table = get_feishu_binding_table()
    user_id = table.get_user_id_by_open_id(open_id, tenant_id=app_cfg.tenant_id)

    if not user_id:
        reply_text_message(
            message_id,
            "尚未绑定飞书账号：请在 MatMaster 网页端登录后，于集成设置中完成绑定。",
            tenant_token=tenant_token,
        )
        return

    session_id = _session_id_for_chat(chat_id or "p2p", open_id)

    await _run_agent_and_reply_feishu(
        user_id=user_id,
        session_id=session_id,
        user_prompt=user_prompt,
        message_id=message_id,
        tenant_token=tenant_token,
    )


def _verify_event_token(parsed: dict[str, Any], verify_token: str | None) -> bool:
    if not verify_token:
        return True
    tok: str | None = None
    if parsed.get("type") == "url_verification":
        tok = parsed.get("token")
    else:
        header = parsed.get("header") or {}
        if isinstance(header, dict):
            tok = header.get("token")
    if tok is None:
        return True
    return str(tok) == verify_token


def _load_app_config(tenant_id: str) -> FeishuAppConfig | None:
    table = get_feishu_app_config_table()
    return table.get_by_tenant_id(tenant_id)


async def process_feishu_event_request(
    *,
    tenant_id: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> tuple[dict[str, Any], int, dict[str, Any] | None, FeishuAppConfig | None]:
    """
    处理飞书 POST。
    返回 (JSON 体, HTTP 状态码, 可选 IM 事件体, 关联的 app 配置)。
    """
    app_cfg = _load_app_config(tenant_id)
    if not app_cfg:
        logger.warning("feishu app config not found for tenant_id=%s", tenant_id)
        return ({"msg": "tenant not configured"}, 404, None, None)

    enc_key = app_cfg.encrypt_key or ""

    def _hdr(name: str) -> str | None:
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v
        return None

    if enc_key:
        sig = _hdr("x-lark-signature")
        ts = _hdr("x-lark-request-timestamp")
        nonce = _hdr("x-lark-request-nonce")
        if not verify_lark_signature(ts, nonce, enc_key, raw_body, sig):
            logger.warning(
                "feishu signature verification failed tenant_id=%s", tenant_id
            )
            return ({"msg": "invalid signature"}, 401, None, None)

    try:
        parsed = feishu_parse_event_json(raw_body, enc_key or None)
    except Exception as e:
        logger.warning("feishu parse_event_json failed: %s", e)
        return ({"msg": "invalid body"}, 400, None, None)

    if not _verify_event_token(parsed, app_cfg.verify_token):
        logger.warning("feishu verification token mismatch tenant_id=%s", tenant_id)
        return ({"msg": "invalid token"}, 403, None, None)

    if parsed.get("type") == "url_verification":
        ch = parsed.get("challenge")
        if ch is not None:
            return ({"challenge": ch}, 200, None, None)
        return ({"msg": "no challenge"}, 400, None, None)

    schema = parsed.get("schema")
    header = parsed.get("header") or {}
    event_type = header.get("event_type") if isinstance(header, dict) else None
    if schema == "2.0" and event_type == "im.message.receive_v1":
        event_body = parsed.get("event") or {}
        if isinstance(event_body, dict):
            return ({}, 200, event_body, app_cfg)
        return ({}, 200, None, None)

    return ({}, 200, None, None)
