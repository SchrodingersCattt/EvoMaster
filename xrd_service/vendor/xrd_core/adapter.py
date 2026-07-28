from typing import Optional, Dict, Any
from copy import deepcopy

# 假设你已经把 utils 移到了 src/characterization/utils
from .results import XRDResult

class InMemoryXRDResult(XRDResult):

    def __init__(self, data: Optional[Dict] = None):
        # 不调用父类 __init__，因为它可能涉及 user_id 等数据库字段
        self.data = data if data else {}

    async def save(self):
        # 在内存模式下，save 不需要做任何持久化操作
        pass

    async def init(self):
        pass

    # 覆盖父类方法，确保只操作 self.data
    async def add_raw_data(self, data: dict):
        self.data = data

    async def add_search_result(self, search_result_name: str, search_eles: dict, echart_option: dict):
        self.data["search_result"] = {
            "search_result_name": search_result_name,
            "search_eles": search_eles,
            "echart_option": echart_option
        }

    def get_raw_data(self, sheet_name: Optional[str] = None) -> Optional[dict]:
        if sheet_name is None:
            return self.data
        return self.data.get(sheet_name, None)
