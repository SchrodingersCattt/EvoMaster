from typing import Optional

from pydantic import BaseModel

from src.base.base_res import BaseResponse


class DefaultTabRequest(BaseModel):
    default_tab_id: int


class DefaultTabModel(BaseModel):
    user_id: str
    default_tab_id: int


class DefaultTabResponse(BaseResponse[Optional[DefaultTabModel]]):
    pass


class ClickedOnboardingRequest(BaseModel):
    has_clicked_onboarding: bool


class ClickedOnboardingModel(BaseModel):
    user_id: str
    has_clicked_onboarding: Optional[bool]


class ClickedOnboardingResponse(BaseResponse[Optional[ClickedOnboardingModel]]):
    pass
