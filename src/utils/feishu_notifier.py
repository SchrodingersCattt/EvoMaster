"""飞书群机器人通知：Worker 开始/完成时发群消息。发送失败仅打 log，不抛异常。
环境从 constant.CURRENT_ENV 取，会带在消息前缀中。支持纯文本与 interactive 卡片两种格式。"""

import json
import logging
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.constant import CURRENT_ENV

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = (
    'https://open.feishu.cn/open-apis/bot/v2/hook/a99f6620-6f73-48a6-85d2-65068e057fd1'
)

# 卡片 header 颜色：blue/green/orange/red
CARD_TEMPLATE_BLUE = 'blue'
CARD_TEMPLATE_GREEN = 'green'
CARD_TEMPLATE_ORANGE = 'orange'
CARD_TEMPLATE_RED = 'red'


def _env_prefix() -> str:
    """消息前缀，含环境时如 [MatMaster-uat]，未配置时为 [MatMaster]。"""
    if not (CURRENT_ENV or '').strip():
        return '[MatMaster] '
    return f'[MatMaster-{(CURRENT_ENV or "").strip().lower()}] '


def _send(body: dict) -> None:
    """发送飞书 webhook 请求。失败仅打 log。"""
    req = Request(
        FEISHU_WEBHOOK_URL,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
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


def notify(text: str) -> None:
    """向飞书群发送一条文本消息。若 text 以 [MatMaster] 开头则自动加上当前环境前缀。"""
    if text.startswith('[MatMaster]'):
        text = _env_prefix() + text[len('[MatMaster]') :].lstrip()
    _send({'msg_type': 'text', 'content': {'text': text}})


def _notify_card_impl(
    title: str,
    content_rows: list[tuple[str, str]],
    *,
    template: str = CARD_TEMPLATE_BLUE,
) -> None:
    """发送飞书 interactive 卡片：标题栏（含颜色）+ 正文一行一字段（**标签**: 值），紧凑排版。"""
    title_with_env = _env_prefix().rstrip() + ' ' + title
    content = '\n'.join(f'**{label}**: {value}' for label, value in content_rows)
    body = {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {
                'title': {'tag': 'plain_text', 'content': title_with_env},
                'template': template,
            },
            'elements': [
                {
                    'tag': 'div',
                    'text': {'tag': 'lark_md', 'content': content},
                },
            ],
        },
    }
    _send(body)


def notify_post_async(
    title: str,
    content_rows: list[tuple[str, str]],
    *,
    template: str = CARD_TEMPLATE_BLUE,
) -> None:
    """异步发送飞书 interactive 卡片通知：标题 + 多行「标签: 值」，不阻塞调用方。template 为 header 颜色：blue/green/orange/red。"""
    t = threading.Thread(
        target=_notify_card_impl,
        args=(title, content_rows),
        kwargs={'template': template},
        name='feishu_notify_card',
        daemon=True,
    )
    t.start()


def notify_async(text: str) -> None:
    """异步发送飞书纯文本通知（新起线程），不阻塞调用方。"""
    t = threading.Thread(target=notify, args=(text,), name='feishu_notify', daemon=True)
    t.start()
