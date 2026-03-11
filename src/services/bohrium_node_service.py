"""Bohrium 节点生命周期服务：按需创建节点、等待就绪、会话结束后销毁。

参考 ~/Downloads/start.sh：通过 Open API 创建节点，轮询 list 直到 status=2 就绪，
run 结束时调用删除接口释放节点。删除接口若与文档不一致，可通过环境变量覆盖。
host 由 constant.py 的 BOHRIUM_OPENAPI_HOST 提供（不含 /openapi/v1），请求时拼接版本路径。
"""

import logging
import os
import time
from functools import lru_cache
from typing import Any

import httpx

from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_OPENAPI_HOST

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

    def __init__(self) -> None:
        self._host = BOHRIUM_OPENAPI_HOST.rstrip('/')

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
                f"{self._host}/openapi/v1/node/add",
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
                    f"{self._host}/openapi/v1/node/list",
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

    def _fetch_node_list(self, access_key: str) -> list[dict[str, Any]]:
        """请求 node/list 返回 items，供 get_node_info / get_node_detail 复用。"""
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self._host}/openapi/v1/node/list",
                params={'queryType': 'private'},
                headers={'accessKey': access_key},
            )
            r.raise_for_status()
            data = r.json()
        return (data.get('data') or {}).get('items') or []

    def destroy_untracked_nodes_by_name(
        self,
        access_key: str,
        tracked_node_ids: set[int] | None,
        *,
        node_name: str,
        creator_id: int = 0,
    ) -> list[int]:
        """
        清理用户节点里“名称匹配且未登记到 DB”的节点，返回成功销毁的 node_id 列表。
        """
        target_name = (node_name or '').strip()
        if not target_name:
            return []
        tracked = set(tracked_node_ids or set())
        destroyed: list[int] = []
        for item in self._fetch_node_list(access_key):
            raw_node_id = item.get('nodeId') or item.get('node_id') or item.get('id')
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError):
                continue
            if node_id in tracked:
                continue
            raw_name = item.get('name') or item.get('nodeName') or item.get('node_name')
            current_name = (
                raw_name.strip()
                if isinstance(raw_name, str) and raw_name.strip()
                else ''
            )
            if current_name != target_name:
                continue
            raw_project_id = item.get('projectId') or item.get('project_id')
            try:
                project_id = int(raw_project_id)
            except (TypeError, ValueError):
                logger.warning(
                    'skip destroy untracked node: missing project_id node_id=%s name=%s',
                    node_id,
                    current_name,
                )
                continue
            try:
                self.destroy_node(
                    access_key,
                    node_id,
                    project_id,
                    creator_id=creator_id,
                )
                destroyed.append(node_id)
            except Exception as e:
                logger.warning(
                    'destroy untracked node failed node_id=%s name=%s err=%s',
                    node_id,
                    current_name,
                    e,
                )
        return destroyed

    def get_image_name_by_id(
        self,
        access_key: str,
        image_id: int,
        *,
        name_query: str | None = None,
    ) -> str | None:
        """
        通过 OpenAPI v2 image/private 列表根据镜像 id 解析出镜像名称（name 字段）。
        用于节点复用校验：node/list 只返回 imageName，用期望的 image_id 查到此 name 后与节点 imageName 比较。
        若配置了环境变量 IMAGE_ACCESS_KEY（镜像创建者 AK），则用其请求列表，否则用传入的 access_key。
        """
        name_query = (
            name_query or os.environ.get('BOHRIUM_IMAGE_NAME_QUERY', 'matmaster')
        ).strip() or 'matmaster'
        image_list_ak = (os.environ.get('IMAGE_ACCESS_KEY') or '').strip() or access_key
        url = f"{self._host}/openapi/v2/image/private"
        params = {
            'type': 'image',
            'device': 'container',
            'current': 1,
            'pageSize': 50,
            'page': 1,
            'name': name_query,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(url, params=params, headers={'accessKey': image_list_ak})
                r.raise_for_status()
                data = r.json()
            if data.get('code') != 0:
                return None
            items = (data.get('data') or {}).get('items') or []
            for item in items:
                if item.get('id') == image_id:
                    name = item.get('name')
                    return (
                        name.strip() if isinstance(name, str) and name.strip() else None
                    )
            return None
        except Exception as e:
            logger.warning('get_image_name_by_id image_id=%s failed: %s', image_id, e)
            return None

    def get_node_detail(self, access_key: str, node_id: int) -> dict[str, Any] | None:
        """
        单次调用 node/list 获取该节点原始信息（不论是否就绪）。
        返回包含 image_id 等字段的节点信息，若节点不在列表中返回 None。
        用于连接前校验节点镜像是否与当前期望一致。
        """
        items = self._fetch_node_list(access_key)
        for item in items:
            if str(item.get('nodeId')) == str(node_id):
                image_id = item.get('imageId') or item.get('image_id')
                if image_id is not None:
                    try:
                        image_id = int(image_id)
                    except (TypeError, ValueError):
                        image_id = None
                image_name = item.get('imageName') or item.get('image_name')
                if isinstance(image_name, str):
                    image_name = image_name.strip() or None
                else:
                    image_name = None
                return {
                    'node_id': node_id,
                    'status': item.get('status'),
                    'ip': item.get('ip'),
                    'password': item.get('nodePwd'),
                    'image_id': image_id,
                    'image_name': image_name,
                }
        return None

    def get_node_info(self, access_key: str, node_id: int) -> dict[str, Any] | None:
        """
        单次调用 node/list 获取该节点信息。若 status=2（就绪）返回 {node_id, ip, password, image_id}，否则返回 None。
        用于复用表中有 node_id 时快速判断是否可直接用，不轮询。
        """
        items = self._fetch_node_list(access_key)
        for item in items:
            if str(item.get('nodeId')) == str(node_id):
                if item.get('status') == NODE_STATUS_READY:
                    image_id = item.get('imageId') or item.get('image_id')
                    if image_id is not None:
                        try:
                            image_id = int(image_id)
                        except (TypeError, ValueError):
                            image_id = None
                    image_name = item.get('imageName') or item.get('image_name')
                    if isinstance(image_name, str):
                        image_name = image_name.strip() or None
                    else:
                        image_name = None
                    return {
                        'node_id': node_id,
                        'ip': item.get('ip'),
                        'password': item.get('nodePwd'),
                        'image_id': image_id,
                        'image_name': image_name,
                    }
                return None
        return None

    def restart_node(
        self,
        access_key: str,
        node_id: int,
        project_id: int,
        *,
        creator_id: int = 0,
        device: str = 'container',
        turnoff_after: int | None = None,
        disk_size: int | None = None,
        sku_id: int | None = None,
    ) -> None:
        """
        重启已关机的节点。POST /node/restart/{node_id}，body 与前端重启请求一致。
        若节点不存在或接口返回失败则抛出异常，调用方可根据需要 fallback 到创建新节点。
        """
        turnoff_after = turnoff_after or int(
            os.environ.get('BOHRIUM_TURNOFF_AFTER', DEFAULT_TURNOFF_AFTER)
        )
        disk_size = disk_size or int(
            os.environ.get('BOHRIUM_DISK_SIZE', DEFAULT_DISK_SIZE)
        )
        sku_id = sku_id or int(os.environ.get('BOHRIUM_SKU_ID', DEFAULT_SKU_ID))
        body = {
            'creatorId': creator_id,
            'projectId': project_id,
            'device': device,
            'turnoffAfter': turnoff_after,
            'diskSize': disk_size,
            'skuId': sku_id,
            'isNotebook': False,
            'datasets': [],
        }
        url = f"{self._host}/openapi/v1/node/restart/{node_id}"
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                url,
                headers={
                    'accessKey': access_key,
                    'content-type': 'application/json',
                },
                json=body,
            )
            r.raise_for_status()
            data = r.json() if r.content else {}
        code = data.get('code')
        if code != 0:
            raise RuntimeError(
                f"Bohrium restart node failed: code={code}, response={data}"
            )
        logger.info('Bohrium node restart requested node_id=%s', node_id)

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
        url = f"{self._host}/openapi/v1/node/del/{node_id}"
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
