"""Enumerated v5 question tags (canonical vocabulary for ``QuestionItem.tags``)."""

from __future__ import annotations

from enum import StrEnum


class QuestionTag(StrEnum):
    """Allowed v5 question tags."""

    # 数据来源
    meta_database = 'meta_database'
    # 计算引擎
    eng_vasp = 'eng_vasp'
    eng_abacus = 'eng_abacus'
    eng_gpumd = 'eng_gpumd'
    eng_lammps = 'eng_lammps'
    eng_cp2k = 'eng_cp2k'
    eng_gromacs = 'eng_gromacs'
    # 代码库/工具链
    code_mlip = 'code_mlip'
    # 结构操作
    struct_surface = 'struct_surface'
    struct_build = 'struct_build'
    struct_transform = 'struct_transform'
    struct_molcrys = 'struct_molcrys'
    # 表征
    char_xrd = 'char_xrd'
    char_pl = 'char_pl'
    char_lifetime = 'char_lifetime'
    char_uvvis = 'char_uvvis'
    char_ec_lsv = 'char_ec_lsv'
    char_ec_cv = 'char_ec_cv'
    char_battery_cycling = 'char_battery_cycling'
