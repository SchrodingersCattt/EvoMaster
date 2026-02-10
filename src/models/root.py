from pydantic import BaseModel

from src.base.base_res import BaseResponse


class RootItem(BaseModel):
    description: str


class RootResponse(BaseResponse[RootItem]):
    pass
