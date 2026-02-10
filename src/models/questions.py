from typing import List, Optional

from pydantic import BaseModel

from src.base.base_res import BaseResponse


class QuestionItem(BaseModel):
    id: int
    question: str
    structure_url: Optional[str] = None
    sharing_url: Optional[str] = None
    figure_url: Optional[str] = None
    belonging_en: Optional[str] = None
    question_en: Optional[str] = None
    sharing_url_en: Optional[str] = None


class QuestionsResponse(BaseModel):
    code: int
    data: List[QuestionItem]
    msg: str


class QuestionBelongingRequest(BaseModel):
    belonging: str


class AllBelongingResponse(BaseResponse[list]):
    pass
