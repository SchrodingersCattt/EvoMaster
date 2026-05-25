"""Deterministic validators for MATTER evaluation (optional heavy deps)."""

from .answer_text import (
    check_answer_json_numeric,
    extract_json_block,
    navigate_json_path,
)
from .checkcif import (
    CheckCIFResult,
    check_checkcif_no_a_alerts,
    run_checkcif,
)
from .structure_density import check_density
from .structure_general import (
    check_atom_count,
    check_bond_angle,
    check_bond_count,
    check_bond_length,
    check_cell_param,
    check_coordination_number,
    check_file_count,
    check_formula,
    check_layer_count,
    check_stoichiometry_ratio,
    check_surface_termination,
)
from .structure_molcrys import (
    check_disorder_dan2_integer_formula,
    check_molcrys_local_env,
    check_sc005_other_formulas_in_answer,
    verify_molecular_slab_layer_scaling,
)
from .text_file import (
    check_text_file_contains_all,
    check_text_file_excludes_all,
    check_text_file_kpt_path,
    check_text_file_numeric_range,
    check_text_file_regex,
)
from .vasp_incar import check_vasp_incar

__all__ = [
    # checkcif (IUCr web service)
    'CheckCIFResult',
    'check_checkcif_no_a_alerts',
    'run_checkcif',
    # structure_general (pymatgen-backed + file-system)
    'check_atom_count',
    'check_bond_angle',
    'check_bond_count',
    'check_bond_length',
    'check_cell_param',
    'check_coordination_number',
    'check_density',
    'check_file_count',
    'check_formula',
    'check_layer_count',
    'check_stoichiometry_ratio',
    'check_surface_termination',
    # text_file (plain text file checks)
    'check_text_file_contains_all',
    'check_text_file_excludes_all',
    'check_text_file_kpt_path',
    'check_text_file_numeric_range',
    'check_text_file_regex',
    # answer_text (deterministic checks on the agent's answer string)
    'check_answer_json_numeric',
    'extract_json_block',
    'navigate_json_path',
    # structure_molcrys (MolCrysKit-backed)
    'check_disorder_dan2_integer_formula',
    'check_molcrys_local_env',
    'check_sc005_other_formulas_in_answer',
    'verify_molecular_slab_layer_scaling',
    # vasp_incar (VASP INCAR semantic checks)
    'check_vasp_incar',
]
