"""飞书群机器人通知：任务进入排队、Worker 开始/完成时发群消息。发送失败仅打 log，不抛异常。
环境从 constant.SERVICE_ENV 取，会带在消息前缀中。支持纯文本与 interactive 卡片两种格式。
对 502/503/504 等瞬时错误做有限次重试。"""

import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.constant import SERVICE_ENV
from utils.feishu_webhook import FEISHU_WEBHOOK_URL

logger = logging.getLogger(__name__)

# 卡片 header 颜色：blue/green/orange/red
CARD_TEMPLATE_BLUE = 'blue'
CARD_TEMPLATE_GREEN = 'green'
CARD_TEMPLATE_ORANGE = 'orange'
CARD_TEMPLATE_RED = 'red'


def format_llm_model_for_notify(model: str | None) -> str:
    """渲染本轮模型名，供飞书卡片「模型」行展示。"""
    m = (model or '').strip()
    return m or '默认'


def _fmt_tokens(n: int) -> str:
    """千分位格式化 token 数，便于阅读。"""
    try:
        return f'{int(n):,}'
    except (TypeError, ValueError):
        return str(n)


_CURRENCY_SYMBOLS = {'CNY': '¥', 'USD': '$'}


def _fmt_money(micro: int, currency: str) -> str:
    """micro（百万分之一货币单位）整数渲染为带币种符号的金额，保留 4 位小数。"""
    amount = int(micro or 0) / 1_000_000
    symbol = _CURRENCY_SYMBOLS.get((currency or '').upper())
    if symbol:
        return f'{symbol}{amount:.4f}'
    return f'{amount:.4f} {currency or ""}'.strip()


def _format_cost_row(cost: dict | None) -> tuple[str, str] | None:
    """把 run 全链路费用渲染为飞书卡片行（标签, 值）。无有效费用返回 None。

    费用为 invocation 维度全链路口径（含子 agent / 压缩），与 Token 消耗行的
    root-kernel 口径不同，故单独标注「全链路」。
    """
    if not isinstance(cost, dict) or not cost:
        return None
    settle_micro = int(cost.get('total_amount_settle_micro') or 0)
    if settle_micro <= 0:
        return None
    currency = str(cost.get('settlement_currency') or 'CNY')
    value = f'{_fmt_money(settle_micro, currency)}（全链路）'
    if int(cost.get('missing_price_count') or 0) > 0:
        value += '，部分模型未定价'
    return ('预估费用', value)


def format_usage_rows(usage_summary: dict | None) -> list[tuple[str, str]]:
    """把 run 的 token 消耗摘要格式化为飞书卡片行（标签, 值）。

    入参为 ``agent_run_service`` 构建的 usage 摘要 dict（或 ``None``）；无有效
    usage 时返回空列表，调用方据此决定是否追加行。展示的是 run-level aggregate
    token usage。
    """
    if not usage_summary:
        return []

    prompt = int(usage_summary.get('prompt_tokens') or 0)
    completion = int(usage_summary.get('completion_tokens') or 0)
    total = int(usage_summary.get('total_tokens') or 0) or (prompt + completion)
    cache_read = int(usage_summary.get('cache_read_tokens') or 0)
    cache_write = int(usage_summary.get('cache_write_tokens') or 0)
    reasoning = int(usage_summary.get('reasoning_tokens') or 0)
    num_turns = int(usage_summary.get('num_turns') or 0)

    cost_row = _format_cost_row(usage_summary.get('cost'))

    if total == 0 and prompt == 0 and completion == 0:
        # 无 token 摘要时，仍可单独展示全链路费用（如压缩/子任务消耗）
        return [cost_row] if cost_row else []

    detail_parts = [
        f'输入 {_fmt_tokens(prompt)}',
        f'输出 {_fmt_tokens(completion)}',
    ]
    if cache_read:
        # 命中率分母用输入（prompt）：缓存命中是输入 token 中复用缓存的部分
        if prompt > 0:
            hit_pct = cache_read / prompt * 100
            detail_parts.append(f'缓存命中 {_fmt_tokens(cache_read)} ({hit_pct:.1f}%)')
        else:
            detail_parts.append(f'缓存命中 {_fmt_tokens(cache_read)}')
    if cache_write:
        detail_parts.append(f'缓存写入 {_fmt_tokens(cache_write)}')
    if reasoning:
        detail_parts.append(f'推理 {_fmt_tokens(reasoning)}')

    rows: list[tuple[str, str]] = [
        ('Token 消耗', f'{_fmt_tokens(total)}（{" / ".join(detail_parts)}）'),
    ]
    if num_turns:
        rows.append(('LLM 轮数', str(num_turns)))

    last_turn = usage_summary.get('last_turn_usage') or {}
    if isinstance(last_turn, dict) and last_turn:
        lt_prompt = int(last_turn.get('prompt_tokens') or 0)
        lt_completion = int(last_turn.get('completion_tokens') or 0)
        lt_total = int(last_turn.get('total_tokens') or 0) or (
            lt_prompt + lt_completion
        )
        if lt_total:
            rows.append(
                (
                    '末轮 Token',
                    f'{_fmt_tokens(lt_total)}'
                    f'（输入 {_fmt_tokens(lt_prompt)} / 输出 {_fmt_tokens(lt_completion)}）',
                )
            )
    if cost_row:
        rows.append(cost_row)
    return rows


def _env_prefix() -> str:
    """消息前缀，含环境时如 [MatMaster-uat]，未配置时为 [MatMaster]。"""
    if not (SERVICE_ENV or '').strip():
        return '[MatMaster] '
    return f'[MatMaster-{(SERVICE_ENV or "").strip().lower()}] '


# 502/503/504 等瞬时错误重试：最多重试次数、每次间隔（秒）
_SEND_MAX_RETRIES = 3
_SEND_RETRY_DELAYS = (1, 2, 3)


def _retry_or_give_up(attempt: int, kind: str, err: Exception) -> None:
    """重试退避：未到上限则按 _SEND_RETRY_DELAYS sleep，否则打最终告警。"""
    if attempt < _SEND_MAX_RETRIES - 1:
        delay = _SEND_RETRY_DELAYS[attempt]
        logger.info(
            'Feishu notify %s (attempt %s/%s), retry in %ss: %s',
            kind,
            attempt + 1,
            _SEND_MAX_RETRIES,
            delay,
            err,
        )
        time.sleep(delay)
    else:
        logger.warning(
            'Feishu notify failed after %s attempts: %s', _SEND_MAX_RETRIES, err
        )


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
            _retry_or_give_up(attempt, '5xx', e)
        except (URLError, OSError) as e:
            _retry_or_give_up(attempt, 'network error', e)


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
