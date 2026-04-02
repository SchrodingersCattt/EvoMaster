"""Deterministic validators for MATTER evaluation (optional heavy deps)."""

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
    check_sc005_other_formulas_in_answer,
    verify_molecular_slab_layer_scaling,
)

__all__ = [
    # structure_general (pymatgen-backed + file-system)
    'check_atom_count',
    'check_bond_angle',
    'check_bond_count',
    'check_bond_length',
    'check_cell_param',
    'check_coordination_number',
    'check_file_count',
    'check_formula',
    'check_layer_count',
    'check_stoichiometry_ratio',
    'check_surface_termination',
    # structure_molcrys (MolCrysKit-backed)
    'check_disorder_dan2_integer_formula',
    'check_sc005_other_formulas_in_answer',
    'verify_molecular_slab_layer_scaling',
]
