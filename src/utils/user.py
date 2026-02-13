import logging
from functools import partial
from typing import Literal, overload

import httpx
from fastapi import HTTPException, Request, status

from src.utils.constant import BI_URL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@overload
def get_user_id(request: Request, *, required: Literal[True] = True) -> str: ...
@overload
def get_user_id(request: Request, *, required: Literal[False]) -> str | None: ...


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
        logger.info(f"用户上下文: user_id={user_id}")

    return user_id or None


require_user_id = partial(get_user_id, required=True)
optional_user_id = partial(get_user_id, required=False)


def get_email_by_user_id(user_id: str, business_line: str = 'bohrium') -> str:
    """Get user email by user_id from BI API."""
    try:
        params = {'businessLine': business_line}
        url = f"{BI_URL.rstrip('/')}/account_api/users/{user_id}"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.info(f"data: {data}")
            if data.get('code') == 0:
                return data.get('data', {}).get('email')
            return None
    except Exception as e:
        logger.error(f"获取用户邮箱失败: {e}")
        return None


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
        logger.error(f"获取用户名失败: {e}")
        return ''
