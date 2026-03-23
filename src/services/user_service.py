"""用户/组织上下文与关联查询：Request Header、BI 用户信息、Bohrium access_key。"""

import logging
from functools import lru_cache

import httpx
from fastapi import HTTPException, Request, status

from src.utils.constant import ACCOUNT_API_BASE_URL, BOHRIUM_CORE_BASE_URL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
    def require_user_id(request: Request) -> str:
        """FastAPI Depends 用：强制要求 X-User-Id，缺失则 401。"""
        return UserService.get_user_id(request, required=True)  # type: ignore[return-value]

    @staticmethod
    def optional_user_id(request: Request) -> str | None:
        """FastAPI Depends 用：可选 X-User-Id，缺失返回 None。"""
        return UserService.get_user_id(request, required=False)

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
    def get_email_by_user_id(
        user_id: str, business_line: str = 'bohrium'
    ) -> str | None:
        """Get user email by user_id from BI API."""
        try:
            params = {'businessLine': business_line}
            url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                logger.info('data: %s', data)
                if data.get('code') == 0:
                    return data.get('data', {}).get('email')
                return None
        except Exception as e:
            logger.error('获取用户邮箱失败: %s', e)
            return None

    @staticmethod
    def get_username_by_user_id(user_id: str, business_line: str = 'bohrium') -> str:
        """Get user nickname by user_id from BI API."""
        try:
            params = {'businessLine': business_line}
            url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()

            if payload.get('code') != 0:
                return ''

            data = payload.get('data') or {}
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
        """从 BI ``account_api/users/{user_id}`` 取学术码 ``userNo``（与邮箱/昵称同源）。"""
        try:
            params = {'businessLine': business_line}
            url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
            with httpx.Client(timeout=15.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
            if payload.get('code') != 0:
                return None
            data = payload.get('data') or {}
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
            params = {'businessLine': business_line}
            url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
            if payload.get('code') != 0:
                return user_id
            data = payload.get('data') or {}
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
            params = {'businessLine': business_line}
            url = f"{ACCOUNT_API_BASE_URL}/account_api/users/{user_id}"
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
            if payload.get('code') != 0:
                return result
            data = payload.get('data') or {}
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
        调用 GET {BOHRIUM_CORE_BASE_URL}/api/v1/ak/list，
        Header: X-User-Id, X-Org-Id，返回该用户/组织下的 access_key（取第一个可用）。

        用于节点复用场景：表里只存 user_id / org_id / project_id / node_id，
        销毁时通过本接口拿到 access_key 再调用 destroy_node。

        Returns:
            access_key 字符串，无可用时返回 None。
        """
        url = f"{BOHRIUM_CORE_BASE_URL.rstrip('/')}/api/v1/ak/list"
        headers = {
            'X-User-Id': str(user_id),
            'X-Org-Id': str(org_id),
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning(
                'get_bohrium_access_key: request failed user_id=%s org_id=%s err=%s',
                user_id,
                org_id,
                e,
            )
            return None

        code = data.get('code', 0)
        if code != 0:
            logger.warning(
                'get_bohrium_access_key: api code=%s user_id=%s org_id=%s',
                code,
                user_id,
                org_id,
            )
            return None

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
            return None

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
                return ak.strip()
        logger.debug(
            'get_bohrium_access_key: no valid ak in items user_id=%s org_id=%s',
            user_id,
            org_id,
        )
        return None


@lru_cache
def get_user_service() -> UserService:
    return UserService()
