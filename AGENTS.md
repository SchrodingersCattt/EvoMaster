# AGENTS.md — AI 编程助手项目约定

本文件为 AI 编程助手提供项目级约定与上下文，请在所有编辑与生成代码时遵守。

---

## Import 规范

**所有 import 必须放在文件最前面。**

- 每个源文件顶部的 import 应紧接在文件开头（可在 shebang、编码声明或 docstring 之后），且**不得**在 import 块之后、再在文件中间或函数/类内部插入新的 import。
- 新增依赖时，将 `import` / `from ... import ...` 统一放在文件顶部的 import 区域，并按项目既有风格分组排序（如：标准库 → 第三方 → 本地包）。

### ✅ 正确示例

```python
# 标准库
import asyncio
import json
from datetime import datetime

# 第三方
from fastapi import FastAPI

# 本地
from src.utils.logger import setup_logging

def main():
    ...
```

### ❌ 避免

```python
def main():
    import json  # 不要写在函数内部
    ...
```

```python
import os

SOME_CONST = 1

import sys  # 不要插在常量或代码中间
```

---

## 异常处理

**应用已在全局做了 error handler，各层异常可向上抛出，由统一异常处理返回给调用方。**

- **DAO 层**：不要用 try/except 捕获并吞掉异常。避免在 DAO 里 `except ...: logger.error(...); return False/0` 等写法，否则上层无法区分“业务无数据”与“数据库错误”。
- **服务层（如调用外部 HTTP 的 quota_service）**：可不在此处捕获，让异常向上抛出，由全局 handler 统一处理；若确有降级需求（如外部不可用时返回默认值），再在调用处或本层按需捕获并写明原因。

---

## 其他约定

（可在此补充项目的其他通用约定，如测试、提交信息、目录结构等。）
