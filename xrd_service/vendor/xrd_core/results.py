import json
from typing import Optional
from copy import deepcopy

class XRDResult():

    async def add_raw_data(self, data: dict):
        self.data = data
        await self.save()

    def get_raw_data(self, sheet_name: Optional[str] = None) -> Optional[dict]:
        if sheet_name is None:
            return self.data
        else:
            return self.data.get(sheet_name, None)

    async def add_search_result(self, search_result_name: str, search_eles: dict, echart_option: dict):
        self.data["search_result"] = {
            "search_result_name": search_result_name,
            "search_eles": search_eles,
            "echart_option": echart_option
        }
        await self.save()

    def get_search_result(self):
        data = self.data.get("search_result", {})
        search_result_name = data.get("search_result_name", None)
        search_eles = data.get("search_eles", None)
        echart_option = data.get("echart_option", None)
        return search_result_name, deepcopy(search_eles), deepcopy(echart_option)

    def to_json(self) -> dict:
        # 验证能否序列化
        json.dumps(self.data)
        return self.data

    @classmethod
    def load_from_json(
            cls, user_id: int, data_id: str, data: Optional[dict]
    ) -> "XRDResult":
        return cls(user_id, data_id, data)

    def is_empty(self, sheet_name: Optional[str] = None):
        if sheet_name is None:
            return not self.data

        return not (sheet_name in self.data)
