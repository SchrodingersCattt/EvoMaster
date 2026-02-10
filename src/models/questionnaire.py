from typing import Optional

from pydantic import BaseModel, Field

from src.base.base_res import BaseResponse


class QuestionnaireRequest(BaseModel):
    """Request model for submitting a user questionnaire."""

    recommendation_rate: str = Field(
        ...,
        pattern='^[ABCDabcd]$',
        description='推荐意愿：A(非常愿意), B(愿意), C(不确定), D(不愿意)',
    )
    core_reason: list[str] = Field(
        default_factory=list, description='核心原因（JSON数组，如 ["原因A", "原因B"]）'
    )
    sub_reason: list[str] = Field(
        default_factory=list, description='其他原因（JSON数组）'
    )
    practical_scenario: list[str] = Field(
        default_factory=list, description='真实好用的场景（JSON数组，仅选AB的有）'
    )
    optimization_suggestion: list[str] = Field(
        default_factory=list, description='需要优化的地方（JSON数组）'
    )

    subject_field: list[str] = Field(
        default_factory=list, description='研究领域（JSON数组）'
    )
    main_functions: list[str] = Field(
        default_factory=list, description='主要使用功能（JSON数组）'
    )
    join_group: bool = Field(False, description='是否愿意加入群')
    wechat: Optional[str] = Field(None, description='微信号')
    contact_info: Optional[str] = Field(None, description='联系方式（手机号/微信）')


class QuestionnaireSubmitData(BaseModel):
    """Response data for a successfully submitted questionnaire."""

    user_id: str
    questionnaire_id: int


class QuestionnaireSubmitResponse(BaseResponse[QuestionnaireSubmitData]):
    """Standard response for questionnaire submission."""
