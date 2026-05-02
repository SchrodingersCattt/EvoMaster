"""Enumerated v5 question tags (canonical vocabulary for ``QuestionItem.tags``)."""

from __future__ import annotations

from enum import StrEnum


class QuestionTag(StrEnum):
    """Allowed v5 question tags."""

    meta_literature = 'meta_literature'
    meta_database = 'meta_database'
    wf_recovery = 'wf_recovery'
    # 软件相关
    eng_vasp = 'eng_vasp'
    eng_abacus = 'eng_abacus'
    wf_postprocess = 'wf_postprocess'
    wf_report = 'wf_report'
    code_pymatgen = 'code_pymatgen'
    code_ase = 'code_ase'
    code_mlip = 'code_mlip'
    eng_gpumd = 'eng_gpumd'
    struct_surface = 'struct_surface'
    char_xrd = 'char_xrd'
    eng_lammps = 'eng_lammps'
    eng_cp2k = 'eng_cp2k'
    eng_gromacs = 'eng_gromacs'
