"""用户/组织上下文与关联查询：Request Header、BI 用户信息、Bohrium access_key。"""

import logging
from functools import lru_cache

import httpx
from fastapi import HTTPException, Request, status

from src.utils.constant import BI_URL, BOHRIUM_CORE_BASE_URL

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
            url = f"{BI_URL.rstrip('/')}/account_api/users/{user_id}"
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
            url = f"{BI_URL.rstrip('/')}/account_api/users/{user_id}"
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
