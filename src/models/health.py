from datetime import datetime

from pydantic import BaseModel

from src.base.base_res import BaseResponse


class HealthComponents(BaseModel):
    database: str


class HealthItem(BaseModel):
    status: str
    timestamp: datetime
    components: HealthComponents


class HealthResponse(BaseResponse[HealthItem]):
    pass
