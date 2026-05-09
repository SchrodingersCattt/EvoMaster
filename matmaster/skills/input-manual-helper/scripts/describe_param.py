"""
describe_param.py — 查询指定参数的详细文档。

Usage
-----
  python describe_param.py --software cp2k --param CUTOFF
  python describe_param.py --software orca --param functional --format json
"""

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from engine.schema import SchemaRegistry  # noqa: E402


def _get_backend(software: str):
    """根据软件名返回对应 Backend 实例。"""
    from engine.software.abinit import ABINITBackend
    from engine.software.cp2k import CP2KBackend
    from engine.software.lammps import LAMMPSBackend
    from engine.software.orca import ORCABackend
    from engine.software.qe import QEBackend

    backends = {
        "cp2k": CP2KBackend,
        "orca": ORCABackend,
        "qe": QEBackend,
        "quantum-espresso": QEBackend,
        "abinit": ABINITBackend,
        "lammps": LAMMPSBackend,
    }
    key = software.lower().strip()
    if key not in backends:
        supported = ", ".join(sorted(set(backends.keys())))
        print(
            f"Error: unsupported software '{software}'. Supported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)
    return backends[key]()


def _tag_to_dict(tag) -> dict:
    """将 ParamTag 转换为普通 dict（用于 JSON 输出）。"""
    d = {
        "name": tag.name,
        "param_type": tag.param_type,
        "default": tag.default,
        "description": tag.description,
        "category": tag.category,
    }
    if tag.section is not None:
        d["section"] = tag.section
    if tag.unit is not None:
        d["unit"] = tag.unit
    if tag.valid_range is not None:
        d["valid_range"] = list(tag.valid_range)
    if tag.enum_values is not None:
        d["enum_values"] = tag.enum_values
    if tag.requires is not None:
        d["requires"] = tag.requires
    if tag.conflicts_with is not None:
        d["conflicts_with"] = tag.conflicts_with
    if tag.doc_url is not None:
        d["doc_url"] = tag.doc_url
    if tag.physical_rules is not None:
        d["physical_rules"] = tag.physical_rules
    return d


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Look up documentation for a calculation parameter."
    )
    parser.add_argument(
        "--software",
        required=True,
        help="Software name: cp2k, orca, qe, abinit, lammps",
    )
    parser.add_argument(
        "--param",
        required=True,
        help="Parameter name to look up (case-insensitive).",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human).",
    )
    args = parser.parse_args()

    _get_backend(args.software)
    schema = SchemaRegistry()
    schema.load_software(args.software.lower())

    tag = schema.get_tag(args.software.lower(), args.param)

    if tag is None:
        print(f"Parameter not found: '{args.param}' in software '{args.software}'")
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(_tag_to_dict(tag), ensure_ascii=False, indent=2))
    else:
        # 调用 ParamTag.to_markdown() 生成人类可读文档
        print(tag.to_markdown())


if __name__ == "__main__":
    main()
