"""
schema.py — 参数元数据 + Schema 注册中心。

ParamTag  : 单个参数的完整元数据，类比 VASP-LSP 的 INCARTag。
SchemaRegistry : 加载并管理所有软件的参数 Schema。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# data/ 目录与本文件的相对位置：engine/ 的上一层是 input-manual-helper/，
# 再下一层是 data/。
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class ParamTag:
    """单个参数的完整元数据，类比 VASP-LSP 的 INCARTag。"""

    name: str
    """参数名"""

    param_type: str
    """'integer' | 'float' | 'boolean' | 'string' | 'array' | 'enum'"""

    default: Any
    """默认值"""

    description: str
    """人类可读说明"""

    category: str
    """'electronic' | 'ionic' | 'parallel' | 'output' | 'system' | ..."""

    section: str | None = None
    """所属 section"""

    valid_range: tuple[float | None, float | None] | None = None
    """数值合法范围 (min, max)；None 表示无限制。"""

    enum_values: list[str] | None = None
    """param_type == 'enum' 时的合法取值列表"""

    unit: str | None = None
    """物理单位，如 'eV', 'Ry', 'bohr', 'angstrom'"""

    requires: list[str] | None = None
    """依赖的其他参数名列表"""

    conflicts_with: list[str] | None = None
    """冲突参数名列表"""

    doc_url: str | None = None
    """官方文档链接"""

    physical_rules: list[str] | None = None
    """物理合理性规则 ID 列表"""

    def to_markdown(self) -> str:
        """生成人类可读的 Markdown 文档字符串。"""
        lines: list[str] = [
            f"## `{self.name}`",
            "",
            self.description,
            "",
            f"- **类型**: `{self.param_type}`",
            f"- **分类**: `{self.category}`",
        ]

        if self.section:
            lines.append(f"- **Section**: `{self.section}`")

        if self.default is not None:
            lines.append(f"- **默认值**: `{self.default}`")

        if self.unit:
            lines.append(f"- **单位**: {self.unit}")

        if self.valid_range is not None:
            lo, hi = self.valid_range
            range_str = (
                f"[{lo if lo is not None else '-∞'}, {hi if hi is not None else '+∞'}]"
            )
            lines.append(f"- **合法范围**: {range_str}")

        if self.enum_values:
            values_str = ", ".join(f"`{v}`" for v in self.enum_values)
            lines.append(f"- **可选值**: {values_str}")

        if self.requires:
            lines.append(f"- **依赖参数**: {', '.join(self.requires)}")

        if self.conflicts_with:
            lines.append(f"- **冲突参数**: {', '.join(self.conflicts_with)}")

        if self.doc_url:
            lines.append(f"- **文档**: [{self.doc_url}]({self.doc_url})")

        if self.physical_rules:
            lines.append(f"- **物理规则**: {', '.join(self.physical_rules)}")

        return "\n".join(lines)

    def to_completion_detail(self) -> str:
        """生成补全候选项的单行 detail 字符串（类似 LSP CompletionItem.detail）。"""
        parts = [f"[{self.param_type}]"]
        if self.default is not None:
            parts.append(f"default={self.default}")
        if self.unit:
            parts.append(self.unit)
        if self.section:
            parts.append(f"@{self.section}")
        return "  ".join(parts)


class SchemaRegistry:
    """加载并管理所有软件的参数 Schema。

    Schema 数据从 ``data/<software>_schema.json`` 文件加载，并解析为
    :class:`ParamTag` 实例缓存在内存中。
    """

    def __init__(self) -> None:
        # { software: { param_name_lower: ParamTag } }
        self._registry: dict[str, dict[str, ParamTag]] = {}

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load_software(self, software: str) -> None:
        """从 ``data/<software>_schema.json`` 加载指定软件的 Schema。

        若已加载则直接返回（幂等）。
        若文件不存在，则注册空字典（不抛出，让调用方决定如何处理）。
        """
        if software in self._registry:
            return

        schema_file = _DATA_DIR / f"{software}_schema.json"
        if not schema_file.exists():
            self._registry[software] = {}
            return

        raw: list[dict[str, Any]] = json.loads(schema_file.read_text(encoding="utf-8"))
        tags: dict[str, ParamTag] = {}
        for entry in raw:
            tag = self._parse_entry(entry)
            tags[tag.name.lower()] = tag

        self._registry[software] = tags

    def _parse_entry(self, entry: dict[str, Any]) -> ParamTag:
        """将 JSON dict 解析为 ParamTag。"""
        valid_range: tuple[float | None, float | None] | None = None
        if "valid_range" in entry and entry["valid_range"] is not None:
            vr = entry["valid_range"]
            valid_range = (vr[0], vr[1])

        return ParamTag(
            name=entry["name"],
            param_type=entry.get("param_type", "string"),
            default=entry.get("default"),
            description=entry.get("description", ""),
            category=entry.get("category", "general"),
            section=entry.get("section"),
            valid_range=valid_range,
            enum_values=entry.get("enum_values"),
            unit=entry.get("unit"),
            requires=entry.get("requires"),
            conflicts_with=entry.get("conflicts_with"),
            doc_url=entry.get("doc_url"),
            physical_rules=entry.get("physical_rules"),
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _ensure_loaded(self, software: str) -> None:
        if software not in self._registry:
            self.load_software(software)

    def get_tag(self, software: str, param_name: str) -> ParamTag | None:
        """精确查找参数（大小写不敏感）。"""
        self._ensure_loaded(software)
        return self._registry.get(software, {}).get(param_name.lower())

    def search_tags(self, software: str, query: str) -> list[ParamTag]:
        """模糊搜索：名称或描述中包含 query（大小写不敏感）的 ParamTag 列表。"""
        self._ensure_loaded(software)
        q = query.lower()
        results: list[ParamTag] = []
        for tag in self._registry.get(software, {}).values():
            if q in tag.name.lower() or q in tag.description.lower():
                results.append(tag)
        return results

    def list_tags(self, software: str, category: str | None = None) -> list[ParamTag]:
        """列出指定软件（及可选分类）的所有 ParamTag，按名称排序。"""
        self._ensure_loaded(software)
        tags = list(self._registry.get(software, {}).values())
        if category is not None:
            tags = [t for t in tags if t.category == category]
        return sorted(tags, key=lambda t: t.name.lower())

    def get_all_categories(self, software: str) -> list[str]:
        """返回指定软件下所有 category 的去重排序列表。"""
        self._ensure_loaded(software)
        cats = {t.category for t in self._registry.get(software, {}).values()}
        return sorted(cats)

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def supported_software(self) -> list[str]:
        """已加载的软件名称列表（按字典序）。

        注意：未调用 load_software 的软件不会出现在此列表中。
        若需扫描 data/ 目录下所有可用的 schema 文件，请使用
        :func:`available_software_in_data_dir`。
        """
        return sorted(self._registry.keys())

    def available_software_in_data_dir(self) -> list[str]:
        """扫描 data/ 目录，返回所有 ``*_schema.json`` 对应的软件名列表。"""
        if not _DATA_DIR.exists():
            return []
        pattern = re.compile(r"^(.+)_schema\.json$")
        names: list[str] = []
        for f in _DATA_DIR.iterdir():
            m = pattern.match(f.name)
            if m:
                names.append(m.group(1))
        return sorted(names)
