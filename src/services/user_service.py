"""用户/组织上下文与关联查询：Request Header、BI 用户信息、Bohrium access_key。"""

import logging
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Annotated, Any

import httpx
from fastapi import Header, HTTPException, Request, status

from src.utils.constant import ACCOUNT_API_BASE_URL, BOHRIUM_CORE_BASE_URL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class BohriumAccessKeyFetchResult:
    status: str
    access_key: str | None = None
    retryable: bool = False
    attempts: int = 1
    http_status: int | None = None
    api_code: int | None = None
    error_message: str | None = None


class UserService:
    """用户上下文与关联查询：Header、BI、Bohrium AK。"""

    @staticmethod
    def get_user_id(request: Request, *, required: bool = True) -> str | None:
        """
        从 Header 中提取 X-User-Id。
        required=True: 强制要求登录（缺失则 401）
        required=False: 允许未登录（缺失则返回 None）
        """
        user_id = request.headers.get('X-User-Id')

        if required and not user_id:
            logger.warning('未找到用户ID，Header中缺少X-User-Id')
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='无法识别用户身份',
            )

        if user_id:
            logger.info('用户上下文: user_id=%s', user_id)

        return user_id or None

    @staticmethod
    def require_user_id(
        x_user_id: Annotated[
            str,
            Header(
                alias='X-User-Id',
                description='当前登录用户 ID，由上游网关注入。',
                examples=['test-user-123'],
            ),
        ],
    ) -> str:
        """FastAPI Depends 用：强制要求 X-User-Id，缺失则 401。"""
        if not x_user_id:
            logger.warning('未找到用户ID，Header中缺少X-User-Id')
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='无法识别用户身份',
            )
        logger.info('用户上下文: user_id=%s', x_user_id)
        return x_user_id

    @staticmethod
    def optional_user_id(
        x_user_id: Annotated[
            str | None,
            Header(
                alias='X-User-Id',
                description='当前登录用户 ID；未登录或匿名访问时可不传。',
                examples=['test-user-123'],
            ),
        ] = None,
    ) -> str | None:
        """FastAPI Depends 用：可选 X-User-Id，缺失返回 None。"""
        if x_user_id:
            logger.info('用户上下文: user_id=%s', x_user_id)
        return x_user_id

    @staticmethod
    def optional_org_id(
        x_org_id: Annotated[
            str | None,
            Header(
                alias='X-Org-Id',
                description='组织 ID，由上游网关注入；发送消息时可选，用于关联 Bohrium 组织。',
                examples=['org-demo'],
            ),
        ] = None,
    ) -> str | None:
        """FastAPI Depends 用：可选 X-Org-Id，缺失返回 None。"""
        return x_org_id

    @staticmethod
    def get_org_id(request: Request, *, required: bool = False) -> str | None:
        """
        从 Header 中提取 X-Org-Id（与 X-User-Id 类似，由上游网关/网关注入）。
        required=False 时缺失返回 None；required=True 时缺失可抛 400，按需使用。
        """
        org_id = request.headers.get('X-Org-Id')
        if required and not org_id:
            logger.warning('Header 中缺少 X-Org-Id')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='缺少组织标识 X-Org-Id',
            )
        return org_id or None

    @staticmethod
    def _fetch_account_user(
        user_id: str,
        *,
        business_line: str = 'bohrium',
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        """GET ``account_api/users/{user_id}``；成功且 ``code==0`` 时返回 ``data`` 字典，否则 ``None``。

        不捕获异常，由调用方按场景记录 error / debug 日志。
        """
        if not (user_id or '').strip():
            return None
        params = {'businessLine': business_line}
        url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get('code') != 0:
            return None
        data = payload.get('data')
        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def get_email_by_user_id(
        user_id: str, business_line: str = 'bohrium'
    ) -> str | None:
        """Get user email by user_id from BI API."""
        try:
            data = UserService._fetch_account_user(
                user_id, business_line=business_line, timeout=30.0
            )
            logger.debug('account user payload: %s', data)
            if not data:
                return None
            return data.get('email')
        except Exception as e:
            logger.error('获取用户邮箱失败: %s', e)
            return None

    @staticmethod
    def get_username_by_user_id(user_id: str, business_line: str = 'bohrium') -> str:
        """Get user nickname by user_id from BI API."""
        try:
            data = UserService._fetch_account_user(
                user_id, business_line=business_line, timeout=30.0
            )
            if not data:
                return ''
            nickname = data.get('nickname')
            if nickname is None:
                return ''
            if isinstance(nickname, str) and nickname.strip() == '':
                return ''
            return nickname
        except Exception as e:
            logger.error('获取用户名失败: %s', e)
            return ''

    @staticmethod
    def get_user_no_by_user_id(
        user_id: str, business_line: str = 'bohrium'
    ) -> str | None:
        """从 BI ``data.userNo`` 取学术码。"""
        try:
            data = UserService._fetch_account_user(
                user_id, business_line=business_line, timeout=15.0
            )
            if not data:
                return None
            user_no = data.get('userNo')
            if isinstance(user_no, str) and user_no.strip():
                return user_no.strip()
            return None
        except Exception as e:
            logger.error('获取用户学术码失败: %s', e)
            return None

    @staticmethod
    def get_user_display_name(
        user_id: str,
        business_line: str = 'bohrium',
        timeout: float = 3.0,
    ) -> str:
        """一次 BI 请求取昵称或邮箱，用于展示「谁提交」；超时或失败则返回 user_id。"""
        if not (user_id or '').strip():
            return '未知'
        try:
            data = UserService._fetch_account_user(
                user_id, business_line=business_line, timeout=timeout
            )
            if not data:
                return user_id
            nickname = data.get('nickname')
            if isinstance(nickname, str) and nickname.strip():
                return f"{nickname.strip()} ({user_id})"
            email = data.get('email')
            if isinstance(email, str) and email.strip():
                return f"{email.strip()} ({user_id})"
            return user_id
        except Exception as e:
            logger.debug('get_user_display_name failed user_id=%s: %s', user_id, e)
            return user_id

    _USER_INFO_PLACEHOLDER = '-'

    @staticmethod
    def get_user_info_for_display(
        user_id: str | None,
        business_line: str = 'bohrium',
        timeout: float = 3.0,
    ) -> dict[str, str]:
        """一次 BI 请求取用户信息，用于飞书等通知展示。返回 user_id、昵称、邮箱，缺失时用占位符 '-'。"""
        ph = UserService._USER_INFO_PLACEHOLDER
        if not (user_id or '').strip():
            return {'user_id': ph, 'nickname': ph, 'email': ph}
        result: dict[str, str] = {
            'user_id': (user_id or '').strip(),
            'nickname': ph,
            'email': ph,
        }
        try:
            data = UserService._fetch_account_user(
                user_id, business_line=business_line, timeout=timeout
            )
            if not data:
                return result
            nickname = data.get('nickname')
            if isinstance(nickname, str) and nickname.strip():
                result['nickname'] = nickname.strip()
            email = data.get('email')
            if isinstance(email, str) and email.strip():
                result['email'] = email.strip()
            return result
        except Exception as e:
            logger.debug('get_user_info_for_display failed user_id=%s: %s', user_id, e)
            return result

    @staticmethod
    def get_bohrium_access_key(user_id: str, org_id: str) -> str | None:
        """
        先 GET {BOHRIUM_CORE_BASE_URL}/api/v1/ak/list（X-User-Id, X-Org-Id），
        取第一个可用 access_key；若列表为空或无效则 POST /api/v1/ak/add 自动创建
        （与 scimaster-bohr-chat 前端 getAKListReq + createAK 一致）。

        用于节点复用场景：表里只存 user_id / org_id / project_id / node_id，
        销毁时通过本接口拿到 access_key 再调用 destroy_node。

        Returns:
            access_key 字符串，无可用时返回 None。
        """
        result = UserService.fetch_bohrium_access_key_result(
            user_id,
            org_id,
            timeout=15.0,
            retry_delays=(),
        )
        return result.access_key

    @staticmethod
    def get_existing_bohrium_access_key(user_id: str, org_id: str) -> str | None:
        """只读取已有 Bohrium access_key；后台 poller 不替用户自动创建 AK。"""
        result = UserService.fetch_bohrium_access_key_result(
            user_id,
            org_id,
            timeout=15.0,
            retry_delays=(),
            create_if_missing=False,
        )
        return result.access_key

    @staticmethod
    def _fetch_bohrium_access_key_once(
        user_id: str,
        org_id: str,
        *,
        timeout: float,
    ) -> BohriumAccessKeyFetchResult:
        url = f"{BOHRIUM_CORE_BASE_URL.rstrip('/')}/api/v1/ak/list"
        if not (user_id or '').strip() or not (org_id or '').strip():
            return BohriumAccessKeyFetchResult(
                status='missing_user_or_org',
                retryable=False,
            )
        headers = {
            'X-User-Id': str(user_id),
            'X-Org-Id': str(org_id),
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, headers=headers)
                data = r.json()
        except httpx.ReadTimeout as e:
            logger.warning(
                'get_bohrium_access_key: request timeout user_id=%s org_id=%s err=%s',
                user_id,
                org_id,
                e,
            )
            return BohriumAccessKeyFetchResult(
                status='timeout',
                retryable=True,
                error_message=str(e),
            )
        except Exception as e:
            logger.warning(
                'get_bohrium_access_key: request failed user_id=%s org_id=%s err=%s',
                user_id,
                org_id,
                e,
            )
            return BohriumAccessKeyFetchResult(
                status='request_error',
                retryable=True,
                error_message=str(e),
            )

        if 500 <= r.status_code:
            logger.warning(
                'get_bohrium_access_key: http_5xx=%s user_id=%s org_id=%s',
                r.status_code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='http_5xx',
                retryable=True,
                http_status=r.status_code,
            )
        if 400 <= r.status_code:
            logger.warning(
                'get_bohrium_access_key: http_4xx=%s user_id=%s org_id=%s',
                r.status_code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='http_4xx',
                retryable=False,
                http_status=r.status_code,
            )

        code = data.get('code', 0)
        if code != 0:
            logger.warning(
                'get_bohrium_access_key: api code=%s user_id=%s org_id=%s',
                code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='api_code_error',
                retryable=True,
                api_code=code,
            )

        raw = data.get('data')
        items = (
            raw
            if isinstance(raw, list)
            else (raw or {}).get('list') if isinstance(raw, dict) else None
        )
        if not items:
            logger.debug(
                'get_bohrium_access_key: no items user_id=%s org_id=%s',
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='no_items',
                retryable=False,
            )

        for item in items:
            if not isinstance(item, dict):
                continue
            ak = item.get('access_key') or item.get('ak') or item.get('accessKey')
            if ak and isinstance(ak, str) and ak.strip():
                logger.info(
                    'get_bohrium_access_key: ok user_id=%s org_id=%s',
                    user_id,
                    org_id,
                )
                return BohriumAccessKeyFetchResult(
                    status='success',
                    access_key=ak.strip(),
                    retryable=False,
                )
        logger.debug(
            'get_bohrium_access_key: no valid ak in items user_id=%s org_id=%s',
            user_id,
            org_id,
        )
        return BohriumAccessKeyFetchResult(
            status='no_valid_ak',
            retryable=False,
        )

    @staticmethod
    def _create_bohrium_access_key_once(
        user_id: str,
        org_id: str,
        *,
        timeout: float,
    ) -> BohriumAccessKeyFetchResult:
        """POST {BOHRIUM_CORE_BASE_URL}/api/v1/ak/add，与 bohrapi/v1/ak/add 同源能力。"""
        url = f"{BOHRIUM_CORE_BASE_URL.rstrip('/')}/api/v1/ak/add"
        if not (user_id or '').strip() or not (org_id or '').strip():
            return BohriumAccessKeyFetchResult(
                status='missing_user_or_org',
                retryable=False,
            )
        headers = {
            'X-User-Id': str(user_id),
            'X-Org-Id': str(org_id),
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, headers=headers, json={})
                data = r.json()
        except httpx.ReadTimeout as e:
            logger.warning(
                'create_bohrium_access_key: request timeout user_id=%s org_id=%s err=%s',
                user_id,
                org_id,
                e,
            )
            return BohriumAccessKeyFetchResult(
                status='ak_create_timeout',
                retryable=True,
                error_message=str(e),
            )
        except Exception as e:
            logger.warning(
                'create_bohrium_access_key: request failed user_id=%s org_id=%s err=%s',
                user_id,
                org_id,
                e,
            )
            return BohriumAccessKeyFetchResult(
                status='ak_create_request_error',
                retryable=True,
                error_message=str(e),
            )

        if 500 <= r.status_code:
            logger.warning(
                'create_bohrium_access_key: http_5xx=%s user_id=%s org_id=%s',
                r.status_code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='ak_create_http_5xx',
                retryable=True,
                http_status=r.status_code,
            )
        if 400 <= r.status_code:
            logger.warning(
                'create_bohrium_access_key: http_4xx=%s user_id=%s org_id=%s',
                r.status_code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='ak_create_http_4xx',
                retryable=False,
                http_status=r.status_code,
            )

        code = data.get('code', 0)
        if code != 0:
            logger.warning(
                'create_bohrium_access_key: api code=%s user_id=%s org_id=%s',
                code,
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='ak_create_api_code_error',
                retryable=False,
                api_code=code,
            )

        raw = data.get('data')
        payload = raw if isinstance(raw, dict) else {}
        ak = payload.get('access_key') or payload.get('accessKey') or payload.get('ak')
        if ak and isinstance(ak, str) and ak.strip():
            logger.info(
                'create_bohrium_access_key: ok user_id=%s org_id=%s',
                user_id,
                org_id,
            )
            return BohriumAccessKeyFetchResult(
                status='success',
                access_key=ak.strip(),
                retryable=False,
            )
        logger.warning(
            'create_bohrium_access_key: no access_key in response user_id=%s org_id=%s',
            user_id,
            org_id,
        )
        return BohriumAccessKeyFetchResult(
            status='ak_create_empty_response',
            retryable=False,
        )

    @staticmethod
    def fetch_bohrium_access_key_result(
        user_id: str | None,
        org_id: str | None,
        *,
        timeout: float = 2.0,
        retry_delays: tuple[float, ...] = (0.5, 1.0),
        create_if_missing: bool = True,
    ) -> BohriumAccessKeyFetchResult:
        attempts = 0
        result = BohriumAccessKeyFetchResult(status='missing_user_or_org')

        for attempt_index in range(len(retry_delays) + 1):
            attempts += 1
            result = UserService._fetch_bohrium_access_key_once(
                str(user_id or ''),
                str(org_id or ''),
                timeout=timeout,
            )
            result = replace(result, attempts=attempts)
            if not result.retryable or result.status == 'success':
                if (
                    create_if_missing
                    and result.status in {'no_items', 'no_valid_ak'}
                    and (user_id or '').strip()
                    and (org_id or '').strip()
                ):
                    create_res = UserService._create_bohrium_access_key_once(
                        str(user_id or ''),
                        str(org_id or ''),
                        timeout=timeout,
                    )
                    attempts += 1
                    create_res = replace(create_res, attempts=attempts)
                    return create_res
                return result
            if attempt_index < len(retry_delays):
                time.sleep(retry_delays[attempt_index])

        return result


@lru_cache
def get_user_service() -> UserService:
    return UserService()
