"""Enumerated v5 question tags (canonical vocabulary for ``QuestionItem.tags``)."""

from __future__ import annotations

from enum import StrEnum


class QuestionTag(StrEnum):
    """Allowed v5 question tags."""

    # 数据来源
    meta_database = 'meta_database'
    # 行为合约：基于证据回答、不编造
    meta_grounding = 'meta_grounding'
    # 计算引擎
    eng_vasp = 'eng_vasp'
    eng_abacus = 'eng_abacus'
    eng_gpumd = 'eng_gpumd'
    eng_lammps = 'eng_lammps'
    eng_cp2k = 'eng_cp2k'
    eng_gromacs = 'eng_gromacs'
    eng_orca = 'eng_orca'
    # 代码库/工具链
    code_mlip = 'code_mlip'
    # 结构操作
    struct_surface = 'struct_surface'
    struct_build = 'struct_build'
    struct_transform = 'struct_transform'
    struct_molcrys = 'struct_molcrys'
    # MD 后处理分析
    analysis_post_md = 'analysis_post_md'
    # 表征
    char_diffraction = 'char_diffraction'
    char_optical_spectrum = 'char_optical_spectrum'
    char_time_resolved = 'char_time_resolved'
    char_electrochem = 'char_electrochem'
    char_battery_cycling = 'char_battery_cycling'
