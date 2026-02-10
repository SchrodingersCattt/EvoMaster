from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from src.base.base_res import BaseResponse


class QuotaType(str, Enum):
    """配额类型枚举"""

    RATE_QUOTA = 'rate_quota'
    SURVEY_QUOTA = 'survey_quota'


# Request
class UseQuotaRequest(BaseModel):
    user_id: str = Field(..., description='用户ID')
    amount: int = Field(1, ge=1, le=100, description='使用数量')


class CreateUserRequest(BaseModel):
    user_id: str = Field(..., description='用户ID')
    email: Optional[str] = Field(None, description='用户邮箱（可选，默认使用 user_id）')
    daily_quota: int = Field(10, ge=1, le=1000, description='每日配额')


# Response
class QuotaResponse(BaseModel):
    code: int
    data: dict
    msg: str


class QuotaInfoItem(BaseModel):
    user_id: str
    used_today: int
    remaining: int
    daily_quota: int
    rate_quota: int = Field(0, description='评分配额（每日刷新）')
    survey_quota: int = Field(0, description='问卷配额（14天有效）')
    last_reset_date: str
    will_reset_in: str
    s_quota_reset_date: datetime | None = Field(
        None, description='Survey quota reset date (source: s_quota_reset_date)'
    )


class InfoResponse(BaseResponse[QuotaInfoItem]):
    pass


class BatchCheckRequest(BaseModel):
    user_ids: List[str] = Field(..., description='用户ID列表')


class BatchQuotaResponse(BaseModel):
    success: bool
    results: dict


class RewardRequest(BaseModel):
    reward_type: Literal['rating', 'survey'] = Field(
        ..., description='奖励类型: rating(评分) 或 survey(问卷)'
    )


class RewardDataItem(BaseModel):
    user_id: str = Field(..., description='用户ID')
    added_quota: int = Field(..., description='增加的配额数量')
    type: QuotaType = Field(..., description='配额类型')


class RewardResponse(BaseResponse[RewardDataItem]):
    pass
