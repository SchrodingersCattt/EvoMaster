"""
renderer.py — RenderIntent 数据类型。

RenderIntent 描述"我想生成什么样的输入文件"，
由上层 Agent/LLM 填写，传递给 SoftwareBackend.render() 使用。
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class RenderIntent:
    """描述输入文件渲染意图的数据类。

    Attributes
    ----------
    software:
        目标软件，如 'cp2k'、'orca'、'qe'、'abinit'、'lammps'。
    task_type:
        计算任务类型，如 'scf'、'opt'、'md'、'band'、'tddft'。
    structure_file:
        结构文件路径（可为 None，表示使用 base_file 中的结构）。
    params:
        用户指定的参数字典，键为参数名，值为目标值。
        后端渲染时会将这些参数合并/覆盖到模板中。
    base_file:
        作为基础模板的输入文件路径（可选）；若提供则在其基础上修改。
    charge:
        体系总电荷数（整数，默认 0）。
    spin_multiplicity:
        自旋多重度（默认 1，即闭壳层单重态）。
    """

    software: str
    task_type: str
    """'scf' | 'opt' | 'md' | 'band' | 'tddft' | ..."""

    structure_file: str | None
    params: dict[str, Any]

    base_file: str | None = None
    charge: int = 0
    spin_multiplicity: int = 1
