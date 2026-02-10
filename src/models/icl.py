from typing import List

from pydantic import BaseModel

from src.base.base_res import BaseResponse


# Request
class UserQuery(BaseModel):
    query: str


# Response
class SelectExamplesItem(BaseModel):
    input: str
    update_input: str
    toolchain: List[str]
    scene_tags: List[str]


class SelectExamplesResponse(BaseResponse[List[SelectExamplesItem]]):
    pass
