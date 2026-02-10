from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, HttpUrl

from src.base.base_res import BaseResponse


class SessionFilesRequest(BaseModel):
    files: List[str]


class SessionFilesModel(BaseModel):
    session_id: str
    files: List[str]


class SessionFilesResponse(BaseResponse[Optional[SessionFilesModel]]):
    pass


class ConvertFileType(str, Enum):
    pdf = 'pdf'


class ConvertFileRequest(BaseModel):
    url: HttpUrl
    type: ConvertFileType


class FileConvertResult(BaseModel):
    oss_url: str
    oss_path: str
    filename: str


class FileConvertResponse(BaseResponse[Optional[FileConvertResult]]):
    pass
