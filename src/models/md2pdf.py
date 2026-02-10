from enum import Enum
from typing import Optional

from pydantic import BaseModel, HttpUrl

from src.base.base_res import BaseResponse


class ConvertFileType(str, Enum):
    pdf = 'pdf'


class Md2PdfRequest(BaseModel):
    url: HttpUrl
    type: ConvertFileType = ConvertFileType.pdf


class Md2PdfResult(BaseModel):
    oss_url: str
    oss_path: str
    filename: str


class Md2PdfResponse(BaseResponse[Optional[Md2PdfResult]]):
    pass
