"""支持服务通知：会话执行完成/失败时按模板给用户发邮件。发送失败仅打 log，不抛异常。
与飞书通知并行，互不影响。需配置 SUPPORT_SERVICE_BASE_URL（未配置则不发送）。"""

import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.constant import SUPPORT_SERVICE_BASE_URL

logger = logging.getLogger(__name__)

# 会话完成邮件模板 ID（支持服务侧配置）
SESSION_COMPLETE_TEMPLATE_ID = '140'
SEND_CHANNEL = 4
BUSINESS_LINE = 'Bohrium'

_EMAIL_SEND_MAX_RETRIES = 3
_EMAIL_SEND_RETRY_DELAYS = (1, 2, 3)


def _send_session_complete_email_impl(
    session_url: str,
    user_id: str,
    email: str,
    *,
    user_question: str = '',
    submitted_at: str = '',
    duration: str = '',
    result_status: str = '成功',
    fail_reason: str = '',
    completed_at: str = '',
) -> None:
    """同步请求支持服务 /api/template/send，发送会话完成邮件。"""
    if not (SUPPORT_SERVICE_BASE_URL or '').strip():
        logger.debug('Support notify skipped: SUPPORT_SERVICE_BASE_URL not configured')
        return
    session_url = (session_url or '').strip()
    user_id = (user_id or '').strip()
    email = (email or '').strip()
    if not session_url or not user_id or not email:
        logger.debug(
            'Support notify skipped: missing session_url/user_id/email session_url=%s user_id=%s',
            bool(session_url),
            bool(user_id),
        )
        return
    if email == '-':
        logger.debug('Support notify skipped: email is placeholder')
        return
    try:
        user_id_int = int(user_id)
    except ValueError:
        logger.warning(
            'Support notify skipped: user_id not numeric user_id=%s', user_id
        )
        return

    if result_status == '已取消':
        subject_suffix = '您的会话已取消'
    elif result_status == '失败':
        subject_suffix = '您的会话执行失败'
    else:
        subject_suffix = '您的会话已执行完成'

    params = {
        'session_url': session_url,
        'user_question': (user_question or '').strip() or '-',
        'submitted_at': (submitted_at or '').strip() or '-',
        'duration': (duration or '').strip() or '-',
        'result_status': (result_status or '').strip() or '成功',
        'fail_reason': (fail_reason or '').strip() or '-',
        'completed_at': (completed_at or '').strip() or '-',
    }

    url = f'{SUPPORT_SERVICE_BASE_URL.rstrip("/")}/api/template/send'
    body = {
        'userInfo': [
            {
                'userId': user_id_int,
                'email': email,
                'params': params,
            }
        ],
        'templateId': SESSION_COMPLETE_TEMPLATE_ID,
        'sendChannel': SEND_CHANNEL,
        'businessLine': BUSINESS_LINE,
        'emailInfo': {
            'from': 'notice@dpscaas.tech',
            'fromName': 'MatMaster',
            'subject': f'【MatMaster】{subject_suffix}',
            'contentType': 'text/html',
        },
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    for attempt in range(_EMAIL_SEND_MAX_RETRIES):
        try:
            with urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    body_preview = resp.read()[:200]
                    logger.warning(
                        'Support notify HTTP status=%s body=%s',
                        resp.status,
                        body_preview,
                    )
                return
        except HTTPError as e:
            if e.code is None or e.code < 500 or e.code > 599:
                logger.warning('Support notify failed: %s', e)
                return
            if attempt < _EMAIL_SEND_MAX_RETRIES - 1:
                time.sleep(_EMAIL_SEND_RETRY_DELAYS[attempt])
            else:
                logger.warning(
                    'Support notify failed after %s attempts: %s',
                    _EMAIL_SEND_MAX_RETRIES,
                    e,
                )
        except (URLError, OSError) as e:
            if attempt < _EMAIL_SEND_MAX_RETRIES - 1:
                time.sleep(_EMAIL_SEND_RETRY_DELAYS[attempt])
            else:
                logger.warning(
                    'Support notify failed after %s attempts: %s',
                    _EMAIL_SEND_MAX_RETRIES,
                    e,
                )


def send_session_complete_email_async(
    session_url: str,
    user_id: str,
    email: str,
    *,
    user_question: str = '',
    submitted_at: str = '',
    duration: str = '',
    result_status: str = '成功',
    fail_reason: str = '',
    completed_at: str = '',
) -> None:
    """异步发送会话完成邮件（新起线程），不阻塞调用方。成功/失败/取消后均可调用。
    模板可用变量：session_url, user_question, submitted_at, duration, result_status, fail_reason, completed_at。
    """
    threading.Thread(
        target=_send_session_complete_email_impl,
        args=(session_url, user_id, email),
        kwargs={
            'user_question': user_question,
            'submitted_at': submitted_at,
            'duration': duration,
            'result_status': result_status,
            'fail_reason': fail_reason,
            'completed_at': completed_at,
        },
        name='support_notify_session_complete',
        daemon=True,
    ).start()
