from pydantic import BaseModel, Field

from src.base.base_res import BaseResponse


class InvocationStatusUpdateRequest(BaseModel):
    invocation_id: str = Field(..., description='调用唯一标识')
    status_code: int = Field(..., description='状态码，0 或 1')


class InvocationStatusUpdateResponse(BaseResponse[None]):
    pass


class InvocationStatusCheckData(BaseModel):
    invocation_id: str
    status_code: int


class InvocationStatusCheckResponse(BaseResponse[InvocationStatusCheckData]):
    pass
