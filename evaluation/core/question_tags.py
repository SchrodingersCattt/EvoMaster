"""Enumerated v5 question tags (canonical vocabulary for ``QuestionItem.tags``)."""

from __future__ import annotations

from enum import StrEnum


class QuestionTag(StrEnum):
    """Allowed v5 question tags."""

    meta_userlog = 'meta_userlog'
    meta_literature = 'meta_literature'
    meta_database = 'meta_database'
    wf_batch = 'wf_batch'
    wf_sweep = 'wf_sweep'
    wf_recovery = 'wf_recovery'
    # 软件相关
    eng_vasp = 'eng_vasp'
    eng_abacus = 'eng_abacus'
    wf_inputs = 'wf_inputs'
    wf_postprocess = 'wf_postprocess'
    wf_report = 'wf_report'
    code_pymatgen = 'code_pymatgen'
    code_ase = 'code_ase'
    code_mlip = 'code_mlip'
    eng_gpumd = 'eng_gpumd'
    code_dpa = 'code_dpa'
    code_dpgen = 'code_dpgen'
    phy_surface = 'phy_surface'
    phy_phonon = 'phy_phonon'
    phy_electronic = 'phy_electronic'
    phy_defect = 'phy_defect'
    phy_elastic = 'phy_elastic'
    phy_eos = 'phy_eos'
    phy_md = 'phy_md'
    phy_magnetism = 'phy_magnetism'
    mat_perovskite = 'mat_perovskite'
    mat_hea = 'mat_hea'
    mat_oxide = 'mat_oxide'
    mat_polymer = 'mat_polymer'
    mat_mof = 'mat_mof'
    mat_semiconductor = 'mat_semiconductor'
    mat_2d = 'mat_2d'
    mat_alloy = 'mat_alloy'
    char_xrd = 'char_xrd'
    safety_policy = 'safety_policy'
    safety_hazard = 'safety_hazard'
    screening_hte = 'screening_hte'
    log_diagnosis = 'log_diagnosis'
    electrostatics = 'electrostatics'
    hybrid_functional = 'hybrid_functional'
    eng_lammps = 'eng_lammps'
    eng_cp2k = 'eng_cp2k'
    eng_gromacs = 'eng_gromacs'
