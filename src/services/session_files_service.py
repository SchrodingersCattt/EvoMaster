import logging
from typing import List

from src.dao.session_files_db import SessionFilesTable
from src.models.files import SessionFilesResponse

logger = logging.getLogger(__name__)


class SessionFilesService:
    def __init__(self, db: SessionFilesTable):
        self.db = db

    def get_session_files(self, session_id: str) -> SessionFilesResponse:
        return self.db.get_session_files(session_id)

    def set_session_files(self, session_id: str, files: List[str]):
        return self.db.insert_session_files(session_id, files)
