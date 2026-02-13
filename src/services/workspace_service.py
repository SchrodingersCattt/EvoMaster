import re
from urllib.parse import quote

from src.dao.oss_io import get_object_bytes, list_workspace


class WorkspaceService:
    def workspace_list(
        self,
        session_id: str,
        task_id: str,
        path: str = '',
    ):
        OSS_PREFIX = f"matmaster_evo/chat_workspace/{session_id}/{task_id}"
        DOWNLOAD_BASE = f"/chat/sessions/{session_id}/workspace/download"

        raw_entries = list_workspace(OSS_PREFIX, path)
        # 目录在前、文件在后，同类型按 name 排序
        sorted_entries = sorted(
            raw_entries,
            key=lambda e: (
                0 if e.get('type') == 'directory' else 1,
                (e.get('name') or '').lower(),
            ),
        )
        entries = []
        for e in sorted_entries:
            name = e.get('name', '')
            path_rel = e.get('path', '')
            typ = e.get('type', 'file')
            if typ == 'directory':
                entries.append({'type': 'directory', 'name': name, 'path': path_rel})
            else:
                entries.append(
                    {
                        'type': 'file',
                        'name': name,
                        'path': path_rel,
                        'download_url': f"{DOWNLOAD_BASE}?task_id={quote(task_id, safe='')}&path={quote(path_rel, safe='')}",
                    }
                )

        return entries

    def workspace_download(
        self,
        session_id: str,
        task_id: str,
        path: str | None = None,
    ):
        OSS_KEY = f"matmaster_evo/chat_workspace/{session_id}/{task_id}/{path}"
        content = get_object_bytes(OSS_KEY)
        filename = path.split('/')[-1] if '/' in path else path
        if not filename:
            filename = 'download'
        filename = re.sub(r'[^\w.\-]', '_', filename) or 'download'

        return content, filename


def get_workspace_service():
    return WorkspaceService()
