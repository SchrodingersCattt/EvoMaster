import logging

import httpx
from fastapi import HTTPException
from starlette import status
from starlette.requests import Request

from src.models.user import UserContext
from src.utils.constant import BI_URL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_current_user(request: Request) -> UserContext:
    """
    从BohrAPI传递的Header中提取用户信息
    假设BohrAPI会在Header中传递以下信息：
    - X-User-Id: 用户ID
    - X-Username: 用户名（可选）
    - X-User-Role: 用户角色（可选）
    """
    user_id = request.headers.get('X-User-Id')
    logger.info(f"headers = {request.headers}")

    if not user_id:
        # 如果BohrAPI没有传递用户ID，说明鉴权有问题
        logger.warning('未找到用户ID，Header中缺少X-User-Id')
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail='无法识别用户身份'
        )

    logger.info(f"用户上下文: user_id={user_id}")
    return UserContext(user_id=user_id)


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
