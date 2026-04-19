"""ABACUS 后端共享常量与解析辅助函数（由 abacus 模块拆分）。"""

from __future__ import annotations

import re
from typing import Any

from engine.document import ParsedParam, SourceRange

# ---------------------------------------------------------------------------
# ParsedParam compatibility shim
# ---------------------------------------------------------------------------
# DocumentModel uses ParsedParam with fields: name, value, raw_text, range,
# section_path. ABACUS backend also stores `line` as a convenience attribute
# on each param by setting range.start_line. Use _line(p) to get line numbers.


def _line(p: ParsedParam) -> int:
    """Return 1-based line number from ParsedParam."""
    return p.range.start_line if p.range else 0


# ---------------------------------------------------------------------------
# 内建 Si 金刚石结构（conventional cubic cell，a = 5.431 Å）
# 用于 render 时没有提供外部结构文件的占位符
# ---------------------------------------------------------------------------
_SI_ALAT_ANG = 5.431  # Angstrom

# STRU 模板（ABACUS 格式）
# 官方格式参考: https://abacus.deepmodeling.com/en/latest/advanced/input_files/stru.html
_SI_STRU = """\
ATOMIC_SPECIES
Si 28.085 Si_ONCV_PBE-1.0.upf  // label; mass; pseudo_file

NUMERICAL_ORBITAL
Si_gga_9au_100Ry_2s2p1d.orb  // LCAO orbital file (only for basis_type=lcao)

LATTICE_CONSTANT
1.8897259886  // 1 Angstrom in Bohr; lattice vectors below are in Angstrom

LATTICE_VECTORS
{a}  0.0  0.0
0.0  {a}  0.0
0.0  0.0  {a}

ATOMIC_POSITIONS
Cartesian_angstrom  // coordinate unit; also: Direct, Cartesian_au, Cartesian
Si                  // element type
0.0                 // initial magnetic moment (Bohr mag, not spin fraction)
8                   // number of Si atoms (conventional cubic cell)
0.000  0.000  0.000  m 1 1 1
1.358  1.358  1.358  m 1 1 1
2.716  2.716  0.000  m 1 1 1
4.073  4.073  1.358  m 1 1 1
2.716  0.000  2.716  m 1 1 1
4.073  1.358  4.073  m 1 1 1
0.000  2.716  2.716  m 1 1 1
1.358  4.073  4.073  m 1 1 1
""".format(a=_SI_ALAT_ANG)

# KPT 模板（Monkhorst-Pack）— 8×8×8 is safe for both metals and insulators
_SCF_KPT = """\
K_POINTS
0
Gamma
8 8 8 0 0 0
"""

# KPT 模板（高对称 k 路径，用于 band 计算）
# 官方格式: 每行仅有坐标和点数，标签用 // 注释
_BAND_KPT = """\
K_POINTS
6
Line
0.000  0.000  0.000  20  // G (Gamma)
0.500  0.000  0.500  20  // X
0.500  0.250  0.750  20  // W
0.500  0.500  0.500  20  // L
0.000  0.000  0.000  20  // G (Gamma)
0.375  0.375  0.750  1   // K (endpoint)
"""

# ---------------------------------------------------------------------------
# 已知的 ABACUS 参数集（用于 unknown-param 警告豁免）
# ---------------------------------------------------------------------------
_KNOWN_PARAMS: frozenset[str] = frozenset(
    {
        # system
        "suffix",
        "ntype",
        "calculation",
        "esolver_type",
        "pseudo_dir",
        "orbital_dir",
        "stru_file",
        "kpoint_file",
        "symmetry",
        "vdw_method",
        "vdw_s6",
        "kpar",
        "bndpar",
        "init_wfc",
        "mem_saver",
        # electronic
        "ecutwfc",
        "ecutrho",
        "basis_type",
        "nspin",
        "nbands",
        "nelec",
        "dft_functional",
        "smearing_method",
        "smearing_sigma",
        "gamma_only",
        "kspacing",
        "noncolin",
        "lspinorb",
        "ks_solver",
        "pw_diag_thr",
        "pw_diag_nmax",
        "npool",
        "nband_istate",
        "lda_plus_u",
        "hubbard_u",
        "orbital_corr",
        "nupdown",
        "sc_mag_switch",
        # vdw
        "vdw_s8",
        # scf
        "scf_thr",
        "scf_nmax",
        "mixing_type",
        "mixing_beta",
        "mixing_ndim",
        "mixing_gg0",
        "init_chg",
        "scf_os_ndim",
        # ionic / relax
        "force_thr",
        "force_thr_ev",
        "stress_thr",
        "relax_nmax",
        "relax_method",
        "cal_force",
        "cal_stress",
        "fixed_atoms",
        # md
        "md_type",
        "md_nstep",
        "md_dt",
        "md_tfirst",
        "md_tlast",
        "md_tfreq",
        "md_dumpfreq",
        "md_restartfreq",
        "init_vel",
        # output
        "out_chg",
        "out_dos",
        "out_band",
        "out_proj_band",
        "out_stru",
        "out_wfc_lcao",
        "out_dipole",
        "out_mul",
        "out_allband",
        "out_pot",
        "dos_sigma",
        "dos_emin_ev",
        "dos_emax_ev",
        "dos_edelta_ev",
        "dos_nche",
        "printe",
        "berry_phase",
        "gdir",
        "towannier90",
        "wannier_spin",
        "cal_cond",
        # deepks
        "deepks_out_labels",
        "deepks_scf",
        # slab / field / dipole / gate
        "dip_cor_flag",
        "dip_cor_axis",
        "efield_flag",
        "efield_dir",
        "efield_amp",
        "efield_pos_max",
        "efield_pos_dec",
        "gate_flag",
        "zgate",
        "block",
        "block_down",
        "block_up",
        "block_height",
    }
)

# 必须以 INPUT_PARAMETERS 开头的 sentinel
_HEADER_RE = re.compile(r"^\s*INPUT_PARAMETERS\s*$", re.IGNORECASE)

# 注释符号
_COMMENT_CHARS = ("#",)


def _strip_comment(line: str) -> str:
    """Remove inline comment from a line."""
    for ch in _COMMENT_CHARS:
        idx = line.find(ch)
        if idx >= 0:
            return line[:idx]
    return line


def _is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _make_range(line: int, col_start: int, col_end: int) -> SourceRange:
    return SourceRange(
        start_line=line, start_col=col_start, end_line=line, end_col=col_end
    )


def _parse_abacus_value(raw: str) -> Any:
    """尝试将字符串值转为数值或布尔值，否则保留字符串。"""
    stripped = raw.strip()
    # integer
    try:
        return int(stripped)
    except ValueError:
        pass
    # float (including scientific notation like 1e-7)
    try:
        return float(stripped)
    except ValueError:
        pass
    # boolean-like
    low = stripped.lower()
    if low in ("true", ".true."):
        return True
    if low in ("false", ".false."):
        return False
    return stripped
