"""
cp2k_renderer.py — CP2K 输入文件渲染 / 生成逻辑。

从 cp2k.py 拆分而来，包含：
  - render_cp2k_input():  根据 RenderIntent 生成完整 CP2K 输入文件
  - 结构构建辅助（pymatgen / 内建 Si 金刚石）
  - 默认参数、元素 basis/potential 映射等数据
"""

from __future__ import annotations

from typing import Any

from engine.renderer import RenderIntent

# ---------------------------------------------------------------------------
# 内建 Si 金刚石结构（primitive cell，a = 5.431 Å）
# ---------------------------------------------------------------------------
_SI_CELL_A = (0.000000, 2.715500, 2.715500)
_SI_CELL_B = (2.715500, 0.000000, 2.715500)
_SI_CELL_C = (2.715500, 2.715500, 0.000000)

# 分数坐标 → 笛卡尔坐标（Å）
# Si1: (0, 0, 0)
# Si2: (0.25, 0.25, 0.25) → 0.25*(a1+a2+a3)
_SI_COORDS = [
    ("Si", 0.000000, 0.000000, 0.000000),
    ("Si", 1.357750, 1.357750, 1.357750),
]

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    "PROJECT": "cp2k",
    "RUN_TYPE": "ENERGY",
    "PRINT_LEVEL": "MEDIUM",
    "CUTOFF": 300,
    "REL_CUTOFF": 50,
    "NGRIDS": 4,
    "QS_METHOD": "GPW",
    "EPS_DEFAULT": "1.0E-12",
    "SCF_GUESS": "ATOMIC",
    "EPS_SCF": "1.0E-6",
    "MAX_SCF": 50,
    "MAX_DIIS": 7,
    "KPOINTS_SCHEME": "MONKHORST-PACK 4 4 4",
    "BASIS_SET_FILE_NAME": "BASIS_MOLOPT",
    "POTENTIAL_FILE_NAME": "GTH_POTENTIALS",
    "SI_BASIS_SET": "DZVP-MOLOPT-SR-GTH",
    "SI_POTENTIAL": "GTH-PBE-q4",
}

# ---------------------------------------------------------------------------
# 常见元素的默认 basis/potential 映射（GTH-PBE 系列）
# ---------------------------------------------------------------------------
_ELEMENT_BASIS_MAP: dict[str, tuple[str, str]] = {
    "H": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q1"),
    "C": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q4"),
    "N": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q5"),
    "O": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q6"),
    "F": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q7"),
    "Si": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q4"),
    "P": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q5"),
    "S": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q6"),
    "Cl": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q7"),
    "Fe": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q16"),
    "Cu": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q11"),
    "Zn": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q12"),
    "Li": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q3"),
    "Na": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q9"),
    "Mg": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q10"),
    "Al": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q3"),
    "Ca": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q10"),
    "Ti": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q12"),
}

_DEFAULT_BASIS = "DZVP-MOLOPT-SR-GTH"
_DEFAULT_POTENTIAL_PREFIX = "GTH-PBE"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _str(val: Any) -> str:
    return str(val) if val is not None else ""


def _default_basis_potential(element: str, p: dict[str, Any]) -> tuple[str, str]:
    """返回元素对应的默认 basis set 和 pseudopotential 名称。"""
    if element in _ELEMENT_BASIS_MAP:
        return _ELEMENT_BASIS_MAP[element]
    # 对未知元素使用通用 GTH-PBE（不带 -q）
    return _DEFAULT_BASIS, _DEFAULT_POTENTIAL_PREFIX


# ---------------------------------------------------------------------------
# 结构构建辅助
# ---------------------------------------------------------------------------


def _cell_from_pymatgen(struct: Any) -> list[str]:
    latt = struct.lattice
    a = latt.matrix[0]
    b = latt.matrix[1]
    c = latt.matrix[2]
    return [
        f"A  {a[0]:.6f}  {a[1]:.6f}  {a[2]:.6f}",
        f"B  {b[0]:.6f}  {b[1]:.6f}  {b[2]:.6f}",
        f"C  {c[0]:.6f}  {c[1]:.6f}  {c[2]:.6f}",
        "PERIODIC XYZ",
    ]


def _coord_from_pymatgen(struct: Any) -> list[str]:
    lines = []
    for site in struct:
        x, y, z = site.coords
        lines.append(f"{site.specie.symbol}  {x:.6f}  {y:.6f}  {z:.6f}")
    return lines


def _kind_from_pymatgen(struct: Any, p: dict[str, Any]) -> list[tuple[str, list[str]]]:
    elements = sorted({str(site.specie.symbol) for site in struct})
    result: list[tuple[str, list[str]]] = []
    for elem in elements:
        basis, potential = _default_basis_potential(elem, p)
        result.append((elem, [f"BASIS_SET {basis}", f"POTENTIAL {potential}"]))
    return result


def _try_load_structure_pymatgen(
    structure_file: str,
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[tuple[str, list[str]]]] | None:
    """尝试用 pymatgen 加载结构文件；失败时返回 None（优雅降级）。"""
    try:
        from pymatgen.core import Structure  # type: ignore

        struct = Structure.from_file(structure_file)
        cell_lines = _cell_from_pymatgen(struct)
        coord_lines = _coord_from_pymatgen(struct)
        kind_lines = _kind_from_pymatgen(struct, p)
        return cell_lines, coord_lines, kind_lines
    except Exception:  # noqa: BLE001 — 优雅降级
        return None


def _builtin_si_structure(
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[tuple[str, list[str]]]]:
    """内建 Si 金刚石 primitive cell（a = 5.431 Å）。"""
    a1 = _SI_CELL_A
    a2 = _SI_CELL_B
    a3 = _SI_CELL_C
    cell_lines = [
        f"A  {a1[0]:.6f}  {a1[1]:.6f}  {a1[2]:.6f}",
        f"B  {a2[0]:.6f}  {a2[1]:.6f}  {a2[2]:.6f}",
        f"C  {a3[0]:.6f}  {a3[1]:.6f}  {a3[2]:.6f}",
        "PERIODIC XYZ",
    ]
    coord_lines = [f"{elem}  {x:.6f}  {y:.6f}  {z:.6f}" for elem, x, y, z in _SI_COORDS]
    si_basis = _str(p.get("SI_BASIS_SET", "DZVP-MOLOPT-SR-GTH"))
    si_potential = _str(p.get("SI_POTENTIAL", "GTH-PBE-q4"))
    kind_lines: list[tuple[str, list[str]]] = [
        ("Si", [f"BASIS_SET {si_basis}", f"POTENTIAL {si_potential}"]),
    ]
    return cell_lines, coord_lines, kind_lines


def _build_structure(
    intent: RenderIntent,
    p: dict[str, Any],
) -> tuple[list[str], list[str], list[tuple[str, list[str]]]]:
    """构建 &CELL、&COORD 和 &KIND sections 的文本行。

    若 intent.structure_file 非空，尝试用 pymatgen 加载；
    否则或 pymatgen 不可用时，回退到内建 Si 金刚石结构。

    Returns
    -------
    cell_lines:  ``&CELL`` 内部的行（不含 &CELL / &END CELL）
    coord_lines: ``&COORD`` 内部的行
    kind_lines:  list of (element_symbol, [keyword lines])
    """
    if intent.structure_file:
        result = _try_load_structure_pymatgen(intent.structure_file, p)
        if result is not None:
            return result

    return _builtin_si_structure(p)


# ---------------------------------------------------------------------------
# 主渲染函数
# ---------------------------------------------------------------------------


def render_cp2k_input(intent: RenderIntent) -> str:
    """根据 RenderIntent 生成可运行的 CP2K 输入文件。

    - 默认生成 Si 金刚石 SCF 计算
    - intent.params 可覆盖任意默认值
    - intent.structure_file 提供时尝试用 pymatgen 加载结构
    """
    p = dict(_DEFAULTS)
    # 将 intent.params 中的覆盖值合并进来
    for k, v in intent.params.items():
        k_up = k.upper()
        if k_up in p:
            p[k_up] = v
        else:
            # 未知 key 也保存，方便后续扩展
            p[k_up] = v

    run_type = _str(p.get("RUN_TYPE", "ENERGY")).upper()
    task = intent.task_type.lower() if intent.task_type else "scf"
    if task == "opt":
        run_type = "GEO_OPT"
    elif task == "md":
        run_type = "MD"
    elif task in ("band", "bands"):
        run_type = "BAND"
    elif task in ("scf", "energy"):
        run_type = "ENERGY"

    # 结构数据
    cell_lines, coord_lines, kind_lines = _build_structure(intent, p)

    # CUTOFF 等数值参数
    cutoff = p.get("CUTOFF", 300)
    rel_cutoff = p.get("REL_CUTOFF", 50)
    ngrids = p.get("NGRIDS", 4)
    eps_scf = p.get("EPS_SCF", "1.0E-6")
    max_scf = p.get("MAX_SCF", 50)
    max_diis = p.get("MAX_DIIS", 7)
    scf_guess = p.get("SCF_GUESS", "ATOMIC")
    eps_default = p.get("EPS_DEFAULT", "1.0E-12")
    qs_method = p.get("QS_METHOD", "GPW")
    kpoints_scheme = p.get("KPOINTS_SCHEME", "MONKHORST-PACK 4 4 4")
    basis_file = p.get("BASIS_SET_FILE_NAME", "BASIS_MOLOPT")
    potential_file = p.get("POTENTIAL_FILE_NAME", "GTH_POTENTIALS")
    project = p.get("PROJECT", "cp2k")
    print_level = p.get("PRINT_LEVEL", "MEDIUM")

    lines: list[str] = []

    # ---- 文件头注释 ----
    lines += [
        "# CP2K input file — Si diamond DFT (primitive FCC cell)",
        "# Generated by input-manual-helper engine (CP2KBackend)",
        "# Bohrium command: OMP_NUM_THREADS=1 mpirun -np 4 /opt/cp2k/exe/local/cp2k.psmp -i input.inp",
        "",
    ]

    # ---- &GLOBAL ----
    lines += [
        "&GLOBAL",
        f"  PROJECT {project}",
        f"  RUN_TYPE {run_type}",
        f"  PRINT_LEVEL {print_level}",
        "&END GLOBAL",
        "",
    ]

    # ---- &FORCE_EVAL ----
    lines += ["&FORCE_EVAL", "  METHOD Quickstep", "  &DFT"]
    lines += [
        f"    BASIS_SET_FILE_NAME {basis_file}",
        f"    POTENTIAL_FILE_NAME {potential_file}",
    ]
    if intent.charge != 0:
        lines.append(f"    CHARGE {intent.charge}")
    if intent.spin_multiplicity != 1:
        lines.append(f"    MULTIPLICITY {intent.spin_multiplicity}")
        lines.append("    UKS .TRUE.")

    # &MGRID
    lines += [
        "    &MGRID",
        f"      CUTOFF {cutoff}",
        f"      REL_CUTOFF {rel_cutoff}",
        f"      NGRIDS {ngrids}",
        "    &END MGRID",
    ]

    # &QS
    lines += [
        "    &QS",
        f"      METHOD {qs_method}",
        f"      EPS_DEFAULT {eps_default}",
        "    &END QS",
    ]

    # &SCF
    lines += [
        "    &SCF",
        f"      SCF_GUESS {scf_guess}",
        f"      EPS_SCF {eps_scf}",
        f"      MAX_SCF {max_scf}",
        f"      MAX_DIIS {max_diis}",
        "    &END SCF",
    ]

    # &XC → PBE
    lines += [
        "    &XC",
        "      &XC_FUNCTIONAL PBE",
        "      &END XC_FUNCTIONAL",
        "    &END XC",
    ]

    # &KPOINTS
    lines += [
        "    &KPOINTS",
        f"      SCHEME {kpoints_scheme}",
        "    &END KPOINTS",
    ]

    lines += ["  &END DFT"]

    # &SUBSYS
    lines += ["  &SUBSYS"]
    lines += ["    &CELL"] + [f"      {ln}" for ln in cell_lines] + ["    &END CELL"]
    lines += ["    &COORD"] + [f"      {ln}" for ln in coord_lines] + ["    &END COORD"]
    for kl in kind_lines:
        lines += ["    &KIND " + kl[0]]
        for kline in kl[1]:
            lines.append(f"      {kline}")
        lines += ["    &END KIND"]

    lines += ["  &END SUBSYS"]
    lines += ["&END FORCE_EVAL"]

    # ---- &MOTION（只有非 ENERGY 任务才追加）----
    if run_type == "GEO_OPT":
        lines += [
            "",
            "&MOTION",
            "  &GEO_OPT",
            "    OPTIMIZER BFGS",
            "    MAX_ITER 200",
            "    MAX_FORCE 4.5E-4",
            "    RMS_FORCE 3.0E-4",
            "  &END GEO_OPT",
            "&END MOTION",
        ]
    elif run_type == "MD":
        temp = p.get("TEMPERATURE", 300.0)
        steps = p.get("STEPS", 1000)
        timestep = p.get("TIMESTEP", 0.5)
        lines += [
            "",
            "&MOTION",
            "  &MD",
            "    ENSEMBLE NVT",
            f"    STEPS {steps}",
            f"    TIMESTEP {timestep}",
            f"    TEMPERATURE {temp}",
            "  &END MD",
            "&END MOTION",
        ]

    lines.append("")
    return "\n".join(lines)
