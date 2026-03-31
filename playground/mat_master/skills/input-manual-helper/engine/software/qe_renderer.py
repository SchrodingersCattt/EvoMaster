"""
qe_renderer.py — Quantum ESPRESSO pw.x 输入文件渲染器。

从 qe.py 拆出的渲染（render）逻辑，包含：
  - render_qe_input(): 根据 RenderIntent 生成 pw.x 输入文本
  - build_structure():  构建 CELL_PARAMETERS / ATOMIC_SPECIES / ATOMIC_POSITIONS
  - 内建 Si 金刚石结构常量、元素质量表、赝势映射表
"""

from __future__ import annotations

from typing import Any

from engine.renderer import RenderIntent

# ---------------------------------------------------------------------------
# 内建 Si 金刚石结构（primitive FCC cell，a = 5.431 Å）
# ---------------------------------------------------------------------------
# 晶格向量（Å）
_SI_CELL_A = (0.000000, 2.715500, 2.715500)
_SI_CELL_B = (2.715500, 0.000000, 2.715500)
_SI_CELL_C = (2.715500, 2.715500, 0.000000)

# 分数坐标
_SI_FRAC_COORDS = [
    ("Si", 0.000000, 0.000000, 0.000000),
    ("Si", 0.250000, 0.250000, 0.250000),
]

# Si 原子质量
_SI_MASS = 28.0855

# Bohrium 镜像 quantum-espresso:7.1 内置赝势路径与文件名
# 镜像路径：/qe-7.1/EPW/examples/sic/pp/Si.pz-vbc.UPF
_SI_PSEUDO = "Si.pz-vbc.UPF"
_BOHRIUM_PSEUDO_DIR = "/qe-7.1/EPW/examples/sic/pp/"

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    # &CONTROL
    "calculation": "scf",
    "prefix": "pwscf",
    "outdir": "./",
    "pseudo_dir": _BOHRIUM_PSEUDO_DIR,
    "tprnfor": False,
    "tstress": False,
    "restart_mode": "from_scratch",
    "verbosity": "low",
    # &SYSTEM
    "ibrav": 0,
    "nat": 2,
    "ntyp": 1,
    "ecutwfc": 30.0,
    "ecutrho": 240.0,
    "occupations": "fixed",
    # &ELECTRONS
    "conv_thr": "1.0d-8",
    "mixing_beta": 0.7,
    "diagonalization": "david",
    "electron_maxstep": 100,
}

# ---------------------------------------------------------------------------
# 元素质量表
# ---------------------------------------------------------------------------

# 常见元素原子质量（u）
_ELEMENT_MASSES: dict[str, float] = {
    "H": 1.00794,
    "He": 4.00260,
    "Li": 6.94100,
    "Be": 9.01218,
    "B": 10.8110,
    "C": 12.0107,
    "N": 14.0067,
    "O": 15.9994,
    "F": 18.9984,
    "Ne": 20.1797,
    "Na": 22.9898,
    "Mg": 24.3050,
    "Al": 26.9815,
    "Si": 28.0855,
    "P": 30.9738,
    "S": 32.0650,
    "Cl": 35.4530,
    "Ar": 39.9480,
    "K": 39.0983,
    "Ca": 40.0780,
    "Ti": 47.8670,
    "V": 50.9415,
    "Cr": 51.9961,
    "Mn": 54.9380,
    "Fe": 55.8450,
    "Co": 58.9332,
    "Ni": 58.6934,
    "Cu": 63.5460,
    "Zn": 65.3800,
    "Ga": 69.7230,
    "Ge": 72.6400,
    "As": 74.9216,
    "Se": 78.9600,
    "Br": 79.9040,
    "Kr": 83.7980,
    "Rb": 85.4678,
    "Sr": 87.6200,
    "Y": 88.9059,
    "Zr": 91.2240,
    "Nb": 92.9064,
    "Mo": 95.9600,
    "Tc": 98.0000,
    "Ru": 101.070,
    "Rh": 102.906,
    "Pd": 106.420,
    "Ag": 107.868,
    "Cd": 112.411,
    "In": 114.818,
    "Sn": 118.710,
    "Sb": 121.760,
    "Te": 127.600,
    "I": 126.904,
    "Xe": 131.293,
    "Cs": 132.905,
    "Ba": 137.327,
    "La": 138.905,
    "Hf": 178.490,
    "Ta": 180.948,
    "W": 183.840,
    "Re": 186.207,
    "Os": 190.230,
    "Ir": 192.217,
    "Pt": 195.078,
    "Au": 196.967,
    "Hg": 200.590,
    "Tl": 204.383,
    "Pb": 207.200,
    "Bi": 208.980,
}

# ---------------------------------------------------------------------------
# 赝势映射表
# ---------------------------------------------------------------------------

# 标准 SSSP 赝势文件名映射（PBE，优先 PAW kjpaw_psl 系列）
_PSEUDO_MAP: dict[str, str] = {
    "H": "H.pbe-rrkjus_psl.1.0.0.UPF",
    "He": "He.pbe-kjpaw_psl.1.0.0.UPF",
    "Li": "Li.pbe-s-kjpaw_psl.1.0.0.UPF",
    "B": "B.pbe-n-kjpaw_psl.1.0.0.UPF",
    "C": "C.pbe-n-kjpaw_psl.1.0.0.UPF",
    "N": "N.pbe-n-kjpaw_psl.1.0.0.UPF",
    "O": "O.pbe-n-kjpaw_psl.1.0.0.UPF",
    "F": "F.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Na": "Na.pbe-spnl-kjpaw_psl.1.0.0.UPF",
    "Mg": "Mg.pbe-spnl-kjpaw_psl.1.0.0.UPF",
    "Al": "Al.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Si": "Si.pbe-n-kjpaw_psl.1.0.0.UPF",
    "P": "P.pbe-n-kjpaw_psl.1.0.0.UPF",
    "S": "S.pbe-nl-kjpaw_psl.1.0.0.UPF",
    "Cl": "Cl.pbe-n-kjpaw_psl.1.0.0.UPF",
    "K": "K.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Ca": "Ca.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Ti": "Ti.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "V": "V.pbe-spnl-kjpaw_psl.1.0.0.UPF",
    "Cr": "Cr.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Mn": "Mn.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Fe": "Fe.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Co": "Co.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Ni": "Ni.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Cu": "Cu.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Zn": "Zn.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Ga": "Ga.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Ge": "Ge.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "As": "As.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Se": "Se.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Sr": "Sr.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Y": "Y.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Zr": "Zr.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Nb": "Nb.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Mo": "Mo.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Pd": "Pd.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Ag": "Ag.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Cd": "Cd.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "In": "In.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Sn": "Sn.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Sb": "Sb.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Te": "Te.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Ba": "Ba.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "La": "La.pbe-spfn-kjpaw_psl.1.0.0.UPF",
    "Hf": "Hf.pbe-spdfn-kjpaw_psl.1.0.0.UPF",
    "W": "W.pbe-spn-kjpaw_psl.1.0.0.UPF",
    "Pt": "Pt.pbe-spfn-kjpaw_psl.1.0.0.UPF",
    "Au": "Au.pbe-n-kjpaw_psl.1.0.0.UPF",
    "Pb": "Pb.pbe-dn-kjpaw_psl.1.0.0.UPF",
    "Bi": "Bi.pbe-dn-kjpaw_psl.1.0.0.UPF",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _element_mass(elem: str) -> float:
    """返回元素原子质量；未知元素返回 1.0。"""
    return _ELEMENT_MASSES.get(elem, 1.0)


def _default_pseudo(elem: str) -> str:
    """返回元素的默认赝势文件名；未知元素使用通用命名约定。"""
    if elem in _PSEUDO_MAP:
        return _PSEUDO_MAP[elem]
    # 通用命名约定（SSSP 风格）
    return f"{elem}.pbe-n-kjpaw_psl.1.0.0.UPF"


def _fmt_float(v: float) -> str:
    """格式化浮点数为 QE 风格（避免科学计数法造成的可读性问题）。"""
    # 极小值用 Fortran d 格式
    if abs(v) < 1e-4 and v != 0.0:
        return f"{v:.1e}".replace("e", "d")
    return f"{v}"


# ---------------------------------------------------------------------------
# 结构构建辅助函数
# ---------------------------------------------------------------------------


def _try_load_structure_pymatgen(
    structure_file: str,
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[str]] | None:
    """尝试用 pymatgen 加载结构文件；失败时返回 None（优雅降级）。"""
    try:
        from pymatgen.core import Structure  # type: ignore

        struct = Structure.from_file(structure_file)
        cell_lines = _cell_from_pymatgen(struct)
        species_lines = _species_from_pymatgen(struct, p)
        positions_lines = _positions_from_pymatgen(struct)
        return cell_lines, species_lines, positions_lines
    except Exception:  # noqa: BLE001 — 优雅降级
        return None


def _cell_from_pymatgen(struct: Any) -> list[str]:
    latt = struct.lattice
    result = []
    for vec in latt.matrix:
        result.append(f"  {vec[0]:.6f}  {vec[1]:.6f}  {vec[2]:.6f}")
    return result


def _species_from_pymatgen(struct: Any, p: dict[str, Any]) -> list[str]:
    elements = sorted({str(site.specie.symbol) for site in struct})
    result = []
    for elem in elements:
        mass = _element_mass(elem)
        pseudo = _default_pseudo(elem)
        result.append(f"{elem}  {mass}  {pseudo}")
    return result


def _positions_from_pymatgen(struct: Any) -> list[str]:
    result = []
    for site in struct:
        fc = site.frac_coords
        result.append(
            f"{site.specie.symbol}  {fc[0]:.6f}  {fc[1]:.6f}  {fc[2]:.6f}"
        )
    return result


def _builtin_si_structure(
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """内建 Si 金刚石 primitive cell（a = 5.431 Å）。"""
    a1 = _SI_CELL_A
    a2 = _SI_CELL_B
    a3 = _SI_CELL_C
    cell_lines = [
        f"{a1[0]:.6f}  {a1[1]:.6f}  {a1[2]:.6f}",
        f"{a2[0]:.6f}  {a2[1]:.6f}  {a2[2]:.6f}",
        f"{a3[0]:.6f}  {a3[1]:.6f}  {a3[2]:.6f}",
    ]
    pseudo = p.get("si_pseudo", _SI_PSEUDO)
    species_lines = [
        f"Si  {_SI_MASS}  {pseudo}",
    ]
    positions_lines = [
        f"{elem}  {x:.6f}  {y:.6f}  {z:.6f}" for elem, x, y, z in _SI_FRAC_COORDS
    ]
    return cell_lines, species_lines, positions_lines


def build_structure(
    intent: RenderIntent,
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """构建 CELL_PARAMETERS、ATOMIC_SPECIES、ATOMIC_POSITIONS 内容。

    Returns
    -------
    cell_lines:     CELL_PARAMETERS 内部行（晶格向量）
    species_lines:  ATOMIC_SPECIES 内部行（元素 质量 赝势）
    positions_lines: ATOMIC_POSITIONS 内部行（元素 分数坐标）
    """
    if intent.structure_file:
        result = _try_load_structure_pymatgen(intent.structure_file, p)
        if result is not None:
            return result

    return _builtin_si_structure(p)


# ---------------------------------------------------------------------------
# 渲染主函数
# ---------------------------------------------------------------------------


def render_qe_input(intent: RenderIntent) -> str:
    """根据 RenderIntent 生成可运行的 QE pw.x 输入文件。

    - 默认生成 Si 金刚石 SCF 计算（primitive FCC cell，2 原子）
    - intent.task_type 决定 calculation 类型
    - intent.params 可覆盖任意默认值
    """
    p = dict(_DEFAULTS)
    for k, v in intent.params.items():
        p[k.lower()] = v

    task = (intent.task_type or "scf").lower()
    if task in ("scf", "energy"):
        p["calculation"] = "scf"
    elif task == "opt":
        p["calculation"] = "relax"
        p.setdefault("tprnfor", True)
    elif task == "vc-relax":
        p["calculation"] = "vc-relax"
        p.setdefault("tprnfor", True)
        p.setdefault("tstress", True)
    elif task == "md":
        p["calculation"] = "md"
        p.setdefault("tprnfor", True)
    elif task in ("band", "bands"):
        p["calculation"] = "bands"
    elif task == "nscf":
        p["calculation"] = "nscf"

    calc = p["calculation"]
    needs_ions = calc in ("relax", "vc-relax", "md", "vc-md")
    needs_cell = calc in ("vc-relax", "vc-md")

    # 结构
    cell_lines, species_lines, positions_lines = build_structure(intent, p)

    lines: list[str] = []

    # ---- &CONTROL ----
    lines.append(
        "# Quantum ESPRESSO pw.x input — Si diamond SCF (primitive FCC cell)"
    )
    lines.append("# Generated by input-manual-helper engine (QEBackend)")
    lines.append(
        "# Pseudopotential: Si.pz-vbc.UPF (Bohrium image: quantum-espresso:7.1)"
    )
    lines.append("#   pseudo_dir = /qe-7.1/EPW/examples/sic/pp/")
    lines.append("# Run with: pw.x -in input.in")
    lines.append("")
    lines.append("&CONTROL")
    lines.append(f"  calculation = '{calc}',")
    lines.append(f"  prefix = '{p.get('prefix', 'pwscf')}',")
    lines.append(f"  outdir = '{p.get('outdir', './')}',")
    lines.append(f"  pseudo_dir = '{p.get('pseudo_dir', _BOHRIUM_PSEUDO_DIR)}',")
    if p.get("tprnfor", False):
        lines.append("  tprnfor = .true.,")
    if p.get("tstress", False):
        lines.append("  tstress = .true.,")
    if needs_ions:
        etot_thr = p.get("etot_conv_thr", "1.0d-5")
        forc_thr = p.get("forc_conv_thr", "1.0d-4")
        lines.append(f"  etot_conv_thr = {etot_thr},")
        lines.append(f"  forc_conv_thr = {forc_thr},")
    lines.append("/")
    lines.append("")

    # ---- &SYSTEM ----
    nat = p.get("nat", 2)
    ntyp = p.get("ntyp", 1)
    ecutwfc = p.get("ecutwfc", 30.0)
    ecutrho = p.get("ecutrho", 4 * float(ecutwfc))

    lines.append("&SYSTEM")
    lines.append("  ibrav = 0,")
    lines.append(f"  nat = {nat},")
    lines.append(f"  ntyp = {ntyp},")
    lines.append(f"  ecutwfc = {ecutwfc},")
    lines.append(f"  ecutrho = {ecutrho},")

    # occupations / smearing
    occupations = p.get("occupations", "fixed")
    lines.append(f"  occupations = '{occupations}',")
    if occupations == "smearing":
        smearing = p.get("smearing", "methfessel-paxton")
        degauss = p.get("degauss", 0.01)
        lines.append(f"  smearing = '{smearing}',")
        lines.append(f"  degauss = {degauss},")

    # nspin（spin_multiplicity > 1 时开启）
    if intent.spin_multiplicity != 1:
        lines.append("  nspin = 2,")

    # 附加用户指定参数（不在上面已写入的）
    _already_written = {
        "ibrav",
        "nat",
        "ntyp",
        "ecutwfc",
        "ecutrho",
        "occupations",
        "smearing",
        "degauss",
        "nspin",
    }
    for k, v in p.items():
        # 只处理 &SYSTEM 参数（简单判断：在 schema 中 section == &SYSTEM）
        if k.lower() in _already_written:
            continue
        # 跳过 &CONTROL / &ELECTRONS / &IONS / &CELL 的参数
        if k.lower() in {
            "calculation",
            "prefix",
            "outdir",
            "pseudo_dir",
            "tprnfor",
            "tstress",
            "restart_mode",
            "verbosity",
            "etot_conv_thr",
            "forc_conv_thr",
            "nstep",
            "conv_thr",
            "mixing_beta",
            "mixing_mode",
            "electron_maxstep",
            "diagonalization",
            "ion_dynamics",
            "upscale",
            "cell_dynamics",
            "press",
        }:
            continue
        # 只写入显式 intent.params 传入的、不在默认集合中的参数
        if k.lower() not in {kk.lower() for kk in _DEFAULTS}:
            _fmt = f"'{v}'" if isinstance(v, str) else v
            lines.append(f"  {k} = {_fmt},")

    lines.append("/")
    lines.append("")

    # ---- &ELECTRONS ----
    conv_thr = p.get("conv_thr", "1.0d-8")
    mixing_beta = p.get("mixing_beta", 0.7)
    diag = p.get("diagonalization", "david")
    electron_maxstep = p.get("electron_maxstep", 100)

    lines.append("&ELECTRONS")
    lines.append(f"  conv_thr = {conv_thr},")
    lines.append(f"  mixing_beta = {mixing_beta},")
    lines.append(f"  diagonalization = '{diag}',")
    lines.append(f"  electron_maxstep = {electron_maxstep},")
    lines.append("/")
    lines.append("")

    # ---- &IONS（仅弛豫/MD）----
    if needs_ions:
        ion_dyn = p.get("ion_dynamics", "bfgs" if "relax" in calc else "verlet")
        lines.append("&IONS")
        lines.append(f"  ion_dynamics = '{ion_dyn}',")
        if calc in ("relax", "vc-relax") and ion_dyn == "bfgs":
            lines.append(f"  upscale = {p.get('upscale', 100.0)},")
        lines.append("/")
        lines.append("")

    # ---- &CELL（仅变胞计算）----
    if needs_cell:
        cell_dyn = p.get("cell_dynamics", "bfgs")
        press = p.get("press", 0.0)
        lines.append("&CELL")
        lines.append(f"  cell_dynamics = '{cell_dyn}',")
        lines.append(f"  press = {press},")
        lines.append("/")
        lines.append("")

    # ---- ATOMIC_SPECIES ----
    lines.append("ATOMIC_SPECIES")
    for sp_line in species_lines:
        lines.append(f"  {sp_line}")
    lines.append("")

    # ---- ATOMIC_POSITIONS ----
    lines.append("ATOMIC_POSITIONS crystal")
    for pos_line in positions_lines:
        lines.append(f"  {pos_line}")
    lines.append("")

    # ---- K_POINTS ----
    kpoints = p.get("kpoints", None)
    if kpoints:
        lines.append("K_POINTS automatic")
        lines.append(f"  {kpoints}")
    else:
        lines.append("K_POINTS automatic")
        lines.append("  4 4 4 0 0 0")
    lines.append("")

    # ---- CELL_PARAMETERS ----
    lines.append("CELL_PARAMETERS angstrom")
    for cell_line in cell_lines:
        lines.append(f"  {cell_line}")
    lines.append("")

    return "\n".join(lines)
