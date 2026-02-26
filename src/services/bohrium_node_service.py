"""Bohrium 节点生命周期服务：按需创建节点、等待就绪、会话结束后销毁。

参考 ~/Downloads/start.sh：通过 Open API 创建节点，轮询 list 直到 status=2 就绪，
run 结束时调用删除接口释放节点。删除接口若与文档不一致，可通过环境变量覆盖。
base URL 由 constant.py 的 BOHRIUM_OPENAPI_BASE_URL 提供（按 SERVICE_ENV 区分 test/prod）。
"""

import logging
import os
import time
from functools import lru_cache
from typing import Any

import httpx

from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_OPENAPI_BASE_URL

logger = logging.getLogger(__name__)

# 与 start.sh 一致：磁盘、自动关机时间等
DEFAULT_DISK_SIZE = 40
DEFAULT_TURNOFF_AFTER = 24
# 默认机器类型对应 SKU（与 start.sh 中 case 一致）
DEFAULT_SKU_ID = 388  # c2_m4_cpu
# 节点就绪状态码（与 start.sh 中 STATUS=2 一致）
NODE_STATUS_READY = 2
# 轮询间隔与最大等待时间（秒）
POLL_INTERVAL = 5
POLL_TIMEOUT = 600  # 10 分钟


class BohriumNodeService:
    """Bohrium 节点创建、就绪等待、销毁。"""

    def __init__(
        self,
        base_url: str | None = None,
        node_delete_path: str | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get('BOHRIUM_BASE_URL') or BOHRIUM_OPENAPI_BASE_URL
        ).rstrip('/')
        # 删除接口：node/del/{nodeId}，可设 BOHRIUM_NODE_DELETE_PATH 覆盖（不含 nodeId，如 node/del）
        self._delete_path = node_delete_path or os.environ.get(
            'BOHRIUM_NODE_DELETE_PATH', 'node/del'
        )

    def create_node(
        self,
        access_key: str,
        project_id: int,
        *,
        name: str | None = None,
        image_id: int | None = None,
        sku_id: int | None = None,
        disk_size: int | None = None,
        turnoff_after: int | None = None,
    ) -> dict[str, Any]:
        """
        创建节点。与 start.sh 中 node/add 请求一致。
        返回 {"node_id": int, "ip": str|None, "password": str|None}，未就绪时 ip/password 可能为空。
        """
        name = name or os.environ.get('BOHRIUM_NODE_NAME', 'matmaster-session')
        image_id = image_id or int(
            os.environ.get('BOHRIUM_IMAGE_ID', BOHRIUM_DEFAULT_IMAGE_ID)
        )
        sku_id = sku_id or int(os.environ.get('BOHRIUM_SKU_ID', DEFAULT_SKU_ID))
        disk_size = disk_size or int(
            os.environ.get('BOHRIUM_DISK_SIZE', DEFAULT_DISK_SIZE)
        )
        turnoff_after = turnoff_after or int(
            os.environ.get('BOHRIUM_TURNOFF_AFTER', DEFAULT_TURNOFF_AFTER)
        )
        payload = {
            'name': name,
            'imageId': image_id,
            'skuId': sku_id,
            'diskSize': disk_size,
            'projectId': project_id,
            'platform': 'ali',
            'device': 'container',
            'turnoffAfter': turnoff_after,
            'datasets': [],
        }
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{self._base_url}/node/add",
                headers={
                    'accessKey': access_key,
                    'content-type': 'application/json',
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        code = data.get('code')
        if code != 0:
            raise RuntimeError(
                f"Bohrium create node failed: code={code}, response={data}"
            )
        info = data.get('data') or {}
        node_id = info.get('id')
        if node_id is None:
            raise RuntimeError(f"Bohrium create node returned no id: {data}")
        logger.info('Bohrium node created node_id=%s', node_id)
        return {'node_id': node_id, 'ip': None, 'password': None}

    def wait_until_ready(
        self,
        access_key: str,
        node_id: int,
        *,
        poll_interval: float = POLL_INTERVAL,
        timeout: float = POLL_TIMEOUT,
    ) -> dict[str, Any]:
        """
        轮询 node/list 直到该节点 status=2（就绪）。返回包含 ip、nodePwd 的节点信息。
        """
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=30.0) as client:
            while time.monotonic() < deadline:
                r = client.get(
                    f"{self._base_url}/node/list",
                    params={'queryType': 'private'},
                    headers={'accessKey': access_key},
                )
                r.raise_for_status()
                data = r.json()
                items = (data.get('data') or {}).get('items') or []
                for item in items:
                    if str(item.get('nodeId')) == str(node_id):
                        status = item.get('status')
                        if status == NODE_STATUS_READY:
                            logger.info(
                                'Bohrium node ready node_id=%s ip=%s',
                                node_id,
                                item.get('ip'),
                            )
                            return {
                                'node_id': node_id,
                                'ip': item.get('ip'),
                                'password': item.get('nodePwd'),
                            }
                        break
                time.sleep(poll_interval)
        raise TimeoutError(
            f"Bohrium node node_id={node_id} did not become ready within {timeout}s"
        )

    def get_node_info(self, access_key: str, node_id: int) -> dict[str, Any] | None:
        """
        单次调用 node/list 获取该节点信息。若 status=2（就绪）返回 {node_id, ip, password}，否则返回 None。
        用于复用表中有 node_id 时快速判断是否可直接用，不轮询。
        """
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self._base_url}/node/list",
                params={'queryType': 'private'},
                headers={'accessKey': access_key},
            )
            r.raise_for_status()
            data = r.json()
        items = (data.get('data') or {}).get('items') or []
        for item in items:
            if str(item.get('nodeId')) == str(node_id):
                if item.get('status') == NODE_STATUS_READY:
                    return {
                        'node_id': node_id,
                        'ip': item.get('ip'),
                        'password': item.get('nodePwd'),
                    }
                return None
        return None

    def destroy_node(
        self,
        access_key: str,
        node_id: int,
        project_id: int,
        *,
        creator_id: int = 0,
        device: str = 'container',
    ) -> None:
        """
        销毁节点。POST node/del/{nodeId}，body 含 creatorId（创建者用户 id）、device、projectId。
        """
        path = self._delete_path.rstrip('/')
        url = f"{self._base_url}/{path}/{node_id}"
        body = {
            'creatorId': creator_id,
            'device': device,
            'projectId': project_id,
        }
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                url,
                headers={
                    'accessKey': access_key,
                    'content-type': 'application/json',
                },
                json=body,
            )
            if r.status_code == 404 or (
                r.is_success and (r.json() or {}).get('code') != 0
            ):
                logger.warning(
                    'Bohrium destroy node node_id=%s status=%s body=%s',
                    node_id,
                    r.status_code,
                    r.text[:200],
                )
                return
            r.raise_for_status()
        logger.info('Bohrium node destroyed node_id=%s', node_id)


@lru_cache
def get_bohrium_node_service() -> BohriumNodeService:
    return BohriumNodeService()
