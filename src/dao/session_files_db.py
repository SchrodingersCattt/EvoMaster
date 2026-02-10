import logging
from typing import List

from src.base.base_table import BaseTable
from src.models.files import SessionFilesModel, SessionFilesResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SessionFilesTable(BaseTable):
    """会话文件表"""

    table_name = 'session_files'

    def __init__(self):
        super().__init__()

    def get_session_files(self, session_id: str) -> SessionFilesResponse:
        """获取会话中的所有文件链接(列表)"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT file_url
                        FROM {self.table_name}
                        WHERE session_id = %s
                        """,
                        (session_id,),
                    )
                    rows = (
                        cursor.fetchall()
                    )  # [{file_url: url1}, {file_url: url2,}, ...]
                    if not rows:
                        return SessionFilesResponse(
                            code=-1, data=None, msg='session_id not found'
                        )
                    return SessionFilesResponse(
                        data=SessionFilesModel(
                            session_id=session_id,
                            files=[r['file_url'] for r in rows],
                        )
                    )
        except BaseException as err:
            logger.error(err)
            return SessionFilesResponse(code=-1, data=None, msg=str(err))

    def insert_session_files(
        self, session_id: str, files: List[str]
    ) -> SessionFilesResponse:
        if not session_id:
            return SessionFilesResponse(code=-1, data=None, msg='session_id is empty')
        if not files:
            return SessionFilesResponse(code=-1, data=None, msg='files is empty')

        values = [(session_id, file_url, file_url) for file_url in files]
        if not values:
            return SessionFilesResponse(
                code=-1, data=None, msg='no valid file_url found'
            )

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        f"""
                        INSERT IGNORE INTO {self.table_name}
                          (session_id, file_url, file_url_sha256)
                        VALUES
                          (%s, %s, UNHEX(SHA2(%s, 256)))
                        """,
                        values,
                    )
                conn.commit()
            return self.get_session_files(session_id)
        except BaseException as err:
            logger.error(err)
            return SessionFilesResponse(code=-1, data=None, msg=str(err))


if __name__ == '__main__':
    session_db = SessionFilesTable()
    session_db.get_session_files(session_id='session_id')
    result = session_db.insert_session_files('session_id', ['file_url'])
    print(result)
