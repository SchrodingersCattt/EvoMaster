"""飞书群机器人通知：任务进入排队、Worker 开始/完成时发群消息。发送失败仅打 log，不抛异常。
环境从 constant.CURRENT_ENV 取，会带在消息前缀中。支持纯文本与 interactive 卡片两种格式。
对 502/503/504 等瞬时错误做有限次重试。"""

import json
import logging
import threading
import time
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


def format_llm_model_for_notify(llm: str | None, model: str | None) -> str:
    """拼接本轮 LLM 配置块与模型名，供飞书卡片「模型」行展示。"""
    m = (model or '').strip()
    l = (llm or '').strip()
    if m and l:
        return f'{m}（{l}）'
    if m:
        return m
    if l:
        return f'LLM: {l}'
    return '默认'


def _env_prefix() -> str:
    """消息前缀，含环境时如 [MatMaster-uat]，未配置时为 [MatMaster]。"""
    if not (CURRENT_ENV or '').strip():
        return '[MatMaster] '
    return f'[MatMaster-{(CURRENT_ENV or "").strip().lower()}] '


# 502/503/504 等瞬时错误重试：最多重试次数、每次间隔（秒）
_SEND_MAX_RETRIES = 3
_SEND_RETRY_DELAYS = (1, 2, 3)


def _send(body: dict) -> None:
    """发送飞书 webhook 请求。对 5xx/网络错误做有限次重试，失败仅打 log。"""
    req = Request(
        FEISHU_WEBHOOK_URL,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    for attempt in range(_SEND_MAX_RETRIES):
        try:
            with urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    body_preview = resp.read()[:200]
                    logger.warning(
                        'Feishu notify HTTP status=%s body=%s',
                        resp.status,
                        body_preview,
                    )
                return
        except HTTPError as e:
            # 仅对 5xx 重试，4xx 不重试
            if e.code is None or e.code < 500 or e.code > 599:
                logger.warning('Feishu notify failed: %s', e)
                return
            if attempt < _SEND_MAX_RETRIES - 1:
                delay = _SEND_RETRY_DELAYS[attempt]
                logger.info(
                    'Feishu notify 5xx (attempt %s/%s), retry in %ss: %s',
                    attempt + 1,
                    _SEND_MAX_RETRIES,
                    delay,
                    e,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    'Feishu notify failed after %s attempts: %s', _SEND_MAX_RETRIES, e
                )
        except (URLError, OSError) as e:
            if attempt < _SEND_MAX_RETRIES - 1:
                delay = _SEND_RETRY_DELAYS[attempt]
                logger.info(
                    'Feishu notify network error (attempt %s/%s), retry in %ss: %s',
                    attempt + 1,
                    _SEND_MAX_RETRIES,
                    delay,
                    e,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    'Feishu notify failed after %s attempts: %s', _SEND_MAX_RETRIES, e
                )


def notify(text: str) -> None:
    """向飞书群发送一条文本消息。若 text 以 [MatMaster] 开头则自动加上当前环境前缀。"""
    if text.startswith('[MatMaster]'):
        text = _env_prefix() + text[len('[MatMaster]') :].lstrip()
    _send({'msg_type': 'text', 'content': {'text': text}})


def _format_row_value(label: str, value: str) -> str:
    """会话地址渲染为「打开会话」链接，点击跳转，不展示长 URL。"""
    if label == '会话地址' and (value or '').strip().startswith('http'):
        return f'[打开会话]({value.strip()})'
    return value


def _notify_card_impl(
    title: str,
    content_rows: list[tuple[str, str]],
    *,
    template: str = CARD_TEMPLATE_BLUE,
) -> None:
    """发送飞书 interactive 卡片：全部项单块 markdown、每行「标签 + 值」，紧凑展示。"""
    title_with_env = _env_prefix().rstrip() + ' ' + title
    parts = [
        f'**{label}**\n{_format_row_value(label, value)}'
        for label, value in content_rows
    ]
    content = '\n'.join(parts)
    elements: list[dict] = [
        {'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}},
    ]
    elements.append({'tag': 'hr'})
    body = {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {
                'title': {'tag': 'plain_text', 'content': title_with_env},
                'template': template,
            },
            'elements': elements,
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
