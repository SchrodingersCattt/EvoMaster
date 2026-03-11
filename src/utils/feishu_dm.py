"""飞书私聊通知：任务完成时按用户邮箱发私信。需企业自建应用 App ID/App Secret，receive_id_type=email。
发送失败仅打 log，不抛异常。未配置凭证时不发。"""

import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.constant import CURRENT_ENV, FEISHU_APP_ID, FEISHU_APP_SECRET

logger = logging.getLogger(__name__)

# 飞书开放平台：获取 tenant_access_token（有效期约 2 小时）
_AUTH_URL = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
# 发送消息（私聊，按邮箱）
_IM_MESSAGES_URL = (
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=email'
)

# 内存缓存：token 与过期时间戳（秒）；多进程/多实例各存一份即可
_token_cache: dict[str, float] = {}
_token_lock = threading.Lock()
# 提前 5 分钟刷新
_TOKEN_REFRESH_BEFORE_SEC = 300


def _env_prefix() -> str:
    """消息前缀，与 feishu_notifier 一致。"""
    if not (CURRENT_ENV or '').strip():
        return '[MatMaster] '
    return f'[MatMaster-{(CURRENT_ENV or "").strip().lower()}] '


def _get_tenant_access_token() -> str | None:
    """获取 tenant_access_token，带缓存与提前刷新。未配置 app_id/app_secret 返回 None。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return None
    now = time.time()
    with _token_lock:
        token = _token_cache.get('token')
        expire = _token_cache.get('expire', 0)
        if token and expire and now < expire - _TOKEN_REFRESH_BEFORE_SEC:
            return token
    req = Request(
        _AUTH_URL,
        data=json.dumps(
            {'app_id': FEISHU_APP_ID, 'app_secret': FEISHU_APP_SECRET}
        ).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning(
                    'Feishu tenant_access_token HTTP status=%s body=%s',
                    resp.status,
                    resp.read()[:200],
                )
                return None
            data = json.loads(resp.read().decode('utf-8'))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as e:
        logger.warning('Feishu tenant_access_token failed: %s', e)
        return None
    token = data.get('tenant_access_token')
    expire_sec = int(data.get('expire', 0))
    if not token or expire_sec <= 0:
        logger.warning('Feishu tenant_access_token response missing token/expire')
        return None
    with _token_lock:
        _token_cache['token'] = token
        _token_cache['expire'] = now + expire_sec
    return token


def _send_dm_impl(to_email: str, text: str) -> None:
    """向指定邮箱用户发飞书私聊（同步）。失败仅打 log。"""
    to_email = (to_email or '').strip()
    if not to_email:
        return
    token = _get_tenant_access_token()
    if not token:
        logger.warning(
            'Feishu DM skip: no tenant_access_token (auth failed or not configured) to_email=%s',
            to_email,
        )
        return
    body = {
        'receive_id': to_email,
        'msg_type': 'text',
        'content': json.dumps({'text': text}, ensure_ascii=False),
    }
    req = Request(
        _IM_MESSAGES_URL,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
        },
        method='POST',
    )
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning(
                    'Feishu DM HTTP status=%s body=%s to_email=%s',
                    resp.status,
                    resp.read()[:200],
                    to_email,
                )
            else:
                logger.info('Feishu DM sent to_email=%s', to_email)
    except (HTTPError, URLError, OSError) as e:
        logger.warning('Feishu DM failed to_email=%s: %s', to_email, e)


def notify_dm_async(to_email: str, text: str) -> None:
    """异步向指定邮箱用户发飞书私聊，不阻塞调用方。未配置凭证时 no-op。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return
    if not to_email or not (to_email.strip()):
        return
    t = threading.Thread(
        target=_send_dm_impl,
        args=(to_email.strip(), text),
        name='feishu_dm',
        daemon=True,
    )
    t.start()


def _notify_task_dm_async(
    user_id: str,
    session_id: str,
    task_id: str,
    started: bool,
    *,
    user_display: str | None = None,
    result: str | None = None,
    worker_id: str | None = None,
) -> None:
    """按 user_id 查邮箱并异步发飞书私聊（任务开始/完成）。文案与群通知对齐：标题 + **标签**: 值。"""
    if not user_id or not (user_id := user_id.strip()):
        return
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return
    from src.services.user_service import UserService

    email = UserService.get_email_by_user_id(user_id)
    if not email or not (email := email.strip()):
        logger.info(
            'Feishu DM skip: no email for user_id=%s session_id=%s',
            user_id,
            session_id,
        )
        return
    prefix = _env_prefix()
    # 与 feishu_notifier 群卡片一致：标题 + 多行「**标签**: 值」；字段与群通知统一为中文
    if started:
        title = 'Worker 开始执行'
        rows = [
            ('会话ID', session_id),
            ('执行节点', worker_id or '-'),
        ]
        if user_display:
            rows.insert(1, ('用户', user_display))
    else:
        title = 'Worker 执行完成'
        rows = [
            ('会话ID', session_id),
            ('执行节点', worker_id or '-'),
            ('结果', result if result else '成功'),
        ]
        if user_display:
            rows.insert(1, ('用户', user_display))
    content = '\n'.join(f'**{label}**: {value}' for label, value in rows)
    text = f'{prefix}{title}\n\n{content}'
    notify_dm_async(email, text)


def notify_task_started_dm_async(
    user_id: str,
    session_id: str,
    task_id: str,
    *,
    user_display: str | None = None,
    worker_id: str | None = None,
) -> None:
    """任务开始时按 user_id 查邮箱并异步发飞书私聊。无邮箱或未配置时 no-op。"""
    _notify_task_dm_async(
        user_id,
        session_id,
        task_id,
        started=True,
        user_display=user_display,
        worker_id=worker_id,
    )


def notify_task_completed_dm_async(
    user_id: str,
    session_id: str,
    task_id: str,
    *,
    user_display: str | None = None,
    result: str = '成功',
    worker_id: str | None = None,
) -> None:
    """任务完成时按 user_id 查邮箱并异步发飞书私聊。无邮箱或未配置时 no-op。"""
    _notify_task_dm_async(
        user_id,
        session_id,
        task_id,
        started=False,
        user_display=user_display,
        result=result,
        worker_id=worker_id,
    )
