import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 工具状态白名单：这些工具的状态始终视为 available
WHITELIST_TOOL_IDS = [
    '000-LLMTOOL-001',
    '041-FILEPARS-005',
]

# 记录最近一次状态检查的错误原因，供 list 接口展示
LAST_ERROR_REASON: Dict[str, Optional[str]] = {}
