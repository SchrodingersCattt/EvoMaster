"""飞书群机器人通知：Worker 开始/完成时发群消息。发送失败仅打 log，不抛异常。
环境从 constant.CURRENT_ENV 取，会带在消息前缀中。"""

import json
import logging
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.constant import CURRENT_ENV

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = (
    'https://open.feishu.cn/open-apis/bot/v2/hook/171cbe39-9702-4063-a2ce-e11adb44cc44'
)


def _env_prefix() -> str:
    """消息前缀，含环境时如 [MatMaster-uat]，未配置时为 [MatMaster]。"""
    if not (CURRENT_ENV or '').strip():
        return '[MatMaster] '
    return f'[MatMaster-{(CURRENT_ENV or "").strip().lower()}] '


def notify(text: str) -> None:
    """向飞书群发送一条文本消息。发送失败时静默（仅打 log）。若 text 以 [MatMaster] 开头则自动加上当前环境前缀。"""
    url = FEISHU_WEBHOOK_URL
    if text.startswith('[MatMaster]'):
        text = _env_prefix() + text[len('[MatMaster]'):].lstrip()
    body = json.dumps(
        {'msg_type': 'text', 'content': {'text': text}},
        ensure_ascii=False,
    ).encode('utf-8')
    req = Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning(
                    'Feishu notify HTTP status=%s body=%s',
                    resp.status,
                    resp.read()[:200],
                )
    except (HTTPError, URLError, OSError) as e:
        logger.warning('Feishu notify failed: %s', e)


def notify_async(text: str) -> None:
    """异步发送飞书通知（新起线程），不阻塞调用方。"""
    t = threading.Thread(target=notify, args=(text,), name='feishu_notify', daemon=True)
    t.start()
