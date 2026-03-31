"""
qe.py — Quantum ESPRESSO (pw.x) 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 Fortran namelist（&NAME ... /）及 card sections
  - render:          生成可运行的 QE pw.x 输入文件（内建 Si 金刚石结构）
  - get_diagnostics: 基于 Schema 的静态校验 + QE 特有物理规则
  - get_completions: 按当前 namelist section 返回参数建议

QE pw.x 输入格式概述
---------------------
  &CONTROL
    calculation = 'scf',
    prefix = 'si',
  /
  &SYSTEM
    ibrav = 0,
    nat = 2,
    ntyp = 1,
    ecutwfc = 30.0,
  /
  &ELECTRONS
    conv_thr = 1.0d-8,
  /
  ATOMIC_SPECIES
    Si  28.0855  Si.pz-vbc.UPF
  ATOMIC_POSITIONS crystal
    Si  0.0  0.0  0.0
    Si  0.25 0.25 0.25
  K_POINTS automatic
    4 4 4 0 0 0
  CELL_PARAMETERS angstrom
    0.000  2.7155  2.7155
    2.7155  0.000  2.7155
    2.7155  2.7155  0.000
"""

from __future__ import annotations

import re
from typing import Any

from engine.completion import CompletionItem
from engine.diagnostics import Diagnostic
from engine.document import DocumentModel, ParsedParam, ParsedSection, SourceRange
from engine.renderer import RenderIntent
from engine.schema import SchemaRegistry
from engine.software.base import SoftwareBackend

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

# 已知的 card section 名称（大写）
_CARD_NAMES: frozenset[str] = frozenset(
    {
        "ATOMIC_SPECIES",
        "ATOMIC_POSITIONS",
        "K_POINTS",
        "CELL_PARAMETERS",
        "CONSTRAINTS",
        "OCCUPATIONS",
        "ATOMIC_FORCES",
        "HUBBARD",
    }
)

# namelist 名称（大写）
_NAMELIST_NAMES: frozenset[str] = frozenset(
    {
        "CONTROL",
        "SYSTEM",
        "ELECTRONS",
        "IONS",
        "CELL",
    }
)

# 每个 namelist 对应的 section_path（不含 &，用于查询 schema）
_SECTION_PATH_MAP: dict[str, str] = {
    "CONTROL": "&CONTROL",
    "SYSTEM": "&SYSTEM",
    "ELECTRONS": "&ELECTRONS",
    "IONS": "&IONS",
    "CELL": "&CELL",
}

# 用于 diagnostics 时豁免"未知参数"检查的内置关键字
_KNOWN_PARAMS: set[str] = {
    # &CONTROL
    "CALCULATION",
    "TITLE",
    "VERBOSITY",
    "RESTART_MODE",
    "WFCDIR",
    "NSTEP",
    "IPRINT",
    "TSTRESS",
    "TPRNFOR",
    "DT",
    "OUTDIR",
    "PREFIX",
    "LKPOINT_DIR",
    "MAX_SECONDS",
    "ETOT_CONV_THR",
    "FORC_CONV_THR",
    "DISK_IO",
    "PSEUDO_DIR",
    "TEFIELD",
    "DIPFIELD",
    "LELFIELD",
    "NBERRYCYC",
    "LORBM",
    "LBERRY",
    "GDIR",
    "NPPSTR",
    "LFCPOPT",
    "MONOPOLE",
    # &SYSTEM
    "IBRAV",
    "CELLDM",
    "A",
    "B",
    "C",
    "COSAB",
    "COSAC",
    "COSBC",
    "NAT",
    "NTYP",
    "NBND",
    "NBND_CRD",
    "NBND_OCC",
    "ECUTWFC",
    "ECUTRHO",
    "NR1",
    "NR2",
    "NR3",
    "NR1S",
    "NR2S",
    "NR3S",
    "NOSYM",
    "NOSYM_EVC",
    "NOINV",
    "NO_T_REV",
    "FORCE_SYMMORPHIC",
    "USE_ALL_FRAC",
    "OCCUPATIONS",
    "ONE_ATOM_OCCUPATIONS",
    "STARTING_SPIN_ANGLE",
    "DEGAUSS",
    "SMEARING",
    "NSPIN",
    "NONCOLIN",
    "ECFIXED",
    "QCUTZ",
    "Q2SIGMA",
    "INPUT_DFT",
    "ACE",
    "EXX_FRACTION",
    "SCREENING_PARAMETER",
    "EXXDIV_TREATMENT",
    "X_GAMMA_EXTRAPOLATION",
    "ECX",
    "EGX",
    "ECUTFOCK",
    "LLOCAL",
    "STARTING_MAGNETIZATION",
    "STARTING_NS_EIGENVALUE",
    "CONSTRAINED_MAGNETIZATION",
    "FIXED_MAGNETIZATION",
    "LAMBDA",
    "REPORT",
    "LSPINORB",
    "ASSUME_ISOLATED",
    "ESYSTEM_MAX",
    "ESYSTEM_AVG",
    "LONDON",
    "LONDON_S6",
    "LONDON_RCO",
    "LONDON_C6",
    "LONDON_RVDW",
    "DFT_D3_VERSION",
    "DFT_D3_3BODY",
    "DFT_D3_RS8",
    "VDWTYPE",
    "LLONDON",
    "LVDW_EFP",
    "LGCSCF",
    "EDIR",
    "EMAXPOS",
    "EOPREG",
    "EAMP",
    "ANGLE1",
    "ANGLE2",
    # &ELECTRONS
    "ELECTRON_MAXSTEP",
    "SCFENV_MAXSTEP",
    "CONV_THR",
    "ADAPTIVE_THR",
    "CONV_THR_INIT",
    "CONV_THR_MULTI",
    "MIXING_MODE",
    "MIXING_BETA",
    "MIXING_NDIM",
    "MIXING_FIXED_NS",
    "DIAGONALIZATION",
    "DIAGO_THR_INIT",
    "DIAGO_CG_MAXITER",
    "DIAGO_PPCG_MAXITER",
    "DIAGO_DAVID_NDX",
    "DIAGO_RMM_NDX",
    "DIAGO_RMM_CONV",
    "DIAGO_GS_NEV",
    "EXX_MAXSTEP",
    "TQSTEP",
    "TQSTEP_N",
    "SCFENV_THR",
    "STARTINGWFC",
    "STARTINGPOT",
    # &IONS
    "ION_DYNAMICS",
    "ION_POSITIONS",
    "POT_EXTRAPOLATION",
    "WFC_EXTRAPOLATION",
    "REMOVE_RIGID_ROT",
    "ION_TEMPERATURE",
    "TEMPW",
    "TOLP",
    "DELTA_T",
    "NRAISE",
    "REFOLD_POS",
    "UPSCALE",
    "BFGS_NDIM",
    "TRUST_RADIUS_MAX",
    "TRUST_RADIUS_MIN",
    "TRUST_RADIUS_INI",
    "W_1",
    "W_2",
    # &CELL
    "CELL_DYNAMICS",
    "PRESS",
    "WMASS",
    "CELL_FACTOR",
    "PRESS_CONV_THR",
    "CELL_DOFREE",
}


# ---------------------------------------------------------------------------
# 解析器辅助函数
# ---------------------------------------------------------------------------


def _parse_value(raw: str) -> Any:
    """将 Fortran namelist 值字符串转换为 Python 原生类型。

    处理：
    - Fortran 字符串：'value' 或 "value"
    - Fortran 布尔：.TRUE./.FALSE.
    - Fortran 科学计数法：1.0d-8 → 1e-8
    - 整数、浮点数
    """
    raw = raw.strip().rstrip(",")
    # 去除 Fortran 字符串引号
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        return raw[1:-1]
    # Fortran 布尔
    if raw.upper() in (".TRUE.", "T", "TRUE", ".T."):
        return True
    if raw.upper() in (".FALSE.", "F", "FALSE", ".F."):
        return False
    # Fortran double 精度科学计数法（1.0d-8 → 1.0e-8）
    raw_norm = raw.replace("d", "e").replace("D", "e")
    # 整数
    try:
        return int(raw)
    except ValueError:
        pass
    # 浮点（包含 d 替换后的形式）
    try:
        return float(raw_norm)
    except ValueError:
        pass
    # 保留原始字符串
    return raw


def _make_range(line: int, col_start: int, col_end: int) -> SourceRange:
    return SourceRange(
        start_line=line,
        start_col=col_start,
        end_line=line,
        end_col=col_end,
    )


def _fmt_float(v: float) -> str:
    """格式化浮点数为 QE 风格（避免科学计数法造成的可读性问题）。"""
    # 极小值用 Fortran d 格式
    if abs(v) < 1e-4 and v != 0.0:
        return f"{v:.1e}".replace("e", "d")
    return f"{v}"


# ---------------------------------------------------------------------------
# QE 后端
# ---------------------------------------------------------------------------


class QEBackend(SoftwareBackend):
    """Quantum ESPRESSO pw.x 输入文件后端。

    完整实现 parse / render / get_diagnostics / get_completions。
    """

    software_name = "qe"

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """解析 QE pw.x 输入文件。

        识别两类结构：
        1. Fortran namelist：``&NAME ... /``
           内部 ``key = value,`` → :class:`ParsedParam`
        2. Card section：``CARD_NAME [options]`` 后跟数据行
           Card 名本身 → :class:`ParsedSection`，内部数据行 → :class:`ParsedParam`
        """
        doc = DocumentModel(
            software="qe",
            source=source,
            raw_text=text,
        )

        lines = text.splitlines()
        flat_params: list[ParsedParam] = []
        top_sections: list[ParsedSection] = []

        # 正则
        _re_namelist_start = re.compile(r"^\s*&([A-Za-z_]\w*)\s*$", re.IGNORECASE)
        _re_namelist_end = re.compile(r"^\s*/\s*$")
        _re_kv = re.compile(  # noqa: F841
            r"^\s*([A-Za-z_]\w*(?:\([^)]*\))?)\s*=\s*(.+)", re.DOTALL
        )
        _re_card = re.compile(r"^([A-Z_]{3,})(\s+.*)?$", re.IGNORECASE)  # noqa: F841

        in_namelist: ParsedSection | None = None  # 当前 namelist section
        in_card: ParsedSection | None = None  # 当前 card section
        card_data_count = 0  # card body 行计数

        for lineno, raw_line in enumerate(lines, start=1):
            # 去除行内注释（! 开头）
            line = raw_line
            bang_idx = line.find("!")
            if bang_idx >= 0:
                line = line[:bang_idx]
            stripped = line.strip()

            if not stripped:
                continue

            # ----------------------------------------------------------------
            # namelist 内部
            # ----------------------------------------------------------------
            if in_namelist is not None:
                # namelist 结束 /
                if _re_namelist_end.match(stripped):
                    in_namelist.range.end_line = lineno
                    in_namelist.range.end_col = len(raw_line)
                    top_sections.append(in_namelist)
                    in_namelist = None
                    continue

                # key = value, 行（可能含多个赋值，如 a=1, b=2,）
                # QE 支持同一行多个赋值：key1=val1, key2=val2,
                # 用逗号分割后逐一解析
                self._parse_namelist_line(
                    stripped, raw_line, lineno, in_namelist, flat_params
                )
                continue

            # ----------------------------------------------------------------
            # card body
            # ----------------------------------------------------------------
            if in_card is not None:
                # 检查是否遇到新的 namelist 或 card（表示当前 card 结束）
                is_new_namelist = _re_namelist_start.match(stripped)
                is_new_card = self._is_card_header(stripped)

                if is_new_namelist or is_new_card:
                    # 结束当前 card
                    in_card.range.end_line = lineno - 1
                    top_sections.append(in_card)
                    in_card = None
                    card_data_count = 0
                    # 继续处理当前行（不 continue）
                else:
                    # card 数据行：用第一个 token（如原子符号）作为 name
                    first_token = (
                        stripped.split()[0]
                        if stripped.split()
                        else f"_row{card_data_count}"
                    )
                    param = ParsedParam(
                        name=first_token,
                        value=stripped,
                        raw_text=raw_line,
                        range=_make_range(lineno, 0, len(raw_line)),
                        section_path=in_card.name,
                    )
                    in_card.params.append(param)
                    flat_params.append(param)
                    card_data_count += 1
                    continue

            # ----------------------------------------------------------------
            # namelist 开始 &NAME
            # ----------------------------------------------------------------
            m_nl = _re_namelist_start.match(stripped)
            if m_nl:
                nl_name = "&" + m_nl.group(1).upper()
                new_sec = ParsedSection(
                    name=nl_name,
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                )
                in_namelist = new_sec
                continue

            # ----------------------------------------------------------------
            # card header
            # ----------------------------------------------------------------
            if self._is_card_header(stripped):
                card_name_raw = stripped.split()[0].upper()
                new_card = ParsedSection(
                    name=card_name_raw,
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                )
                # 将 card 选项（如 'crystal'、'automatic'、'angstrom'）存为参数
                rest_parts = stripped.split(None, 1)
                if len(rest_parts) > 1:
                    option_val = rest_parts[1].strip()
                    opt_param = ParsedParam(
                        name="_option",
                        value=option_val,
                        raw_text=raw_line,
                        range=_make_range(lineno, 0, len(raw_line)),
                        section_path=card_name_raw,
                    )
                    new_card.params.append(opt_param)
                    flat_params.append(opt_param)

                in_card = new_card
                card_data_count = 0
                continue

            # ----------------------------------------------------------------
            # 无法识别的行 → 忽略
            # ----------------------------------------------------------------

        # 处理未正常关闭的 namelist
        if in_namelist is not None:
            doc.parse_errors.append(
                Diagnostic(
                    severity="warning",
                    message=f"Namelist {in_namelist.name} 未正常关闭（缺少 /）",
                    range=_make_range(in_namelist.range.start_line, 0, 0),
                    rule_id="unclosed-namelist",
                )
            )
            top_sections.append(in_namelist)

        # 处理未结束的 card（文件末尾正常结束）
        if in_card is not None:
            in_card.range.end_line = len(lines)
            top_sections.append(in_card)

        doc.sections = top_sections
        doc.params = flat_params
        return doc

    def _is_card_header(self, stripped: str) -> bool:
        """判断是否为 card 开头行（大写字母开头且为已知 card 名）。"""
        first_word = stripped.split()[0].upper() if stripped else ""
        return first_word in _CARD_NAMES

    def _parse_namelist_line(
        self,
        stripped: str,
        raw_line: str,
        lineno: int,
        section: ParsedSection,
        flat_params: list[ParsedParam],
    ) -> None:
        """解析一行 namelist 内容，可能含多个 key=value 赋值。

        QE 允许同一行多个赋值：``a = 1, b = 2,``
        也允许跨行（续行），但此处简化为单行处理。
        """
        # 按逗号分割，每段可能是 key=value
        # 注意：value 中可能含逗号（如 celldm(1) = 10.0, celldm(2) = 0.5,）
        # 策略：按 pattern "key = value" 正则提取所有赋值对
        _re_kv_multi = re.compile(  # noqa: F841
            r"([A-Za-z_]\w*(?:\(\d+\))?)\s*=\s*"
            r"(['\"].*?['\"]|[^,=]+(?:,\s*[^,=\s]+)*?)"
            r"(?=\s*(?:,\s*[A-Za-z_]|\s*$))",
            re.DOTALL,
        )

        sec_path = section.name  # 如 "&CONTROL"

        # 简化方法：先将行按 "，后跟 key=" 切割
        # 先尝试整行作为单个赋值
        assignments: list[tuple[str, str]] = []
        self._extract_assignments(stripped, assignments)

        for kw_name, kw_raw in assignments:
            kw_val = _parse_value(kw_raw)
            param = ParsedParam(
                name=kw_name,
                value=kw_val,
                raw_text=raw_line,
                range=_make_range(lineno, 0, len(raw_line)),
                section_path=sec_path,
            )
            section.params.append(param)
            flat_params.append(param)

    def _extract_assignments(self, line: str, result: list[tuple[str, str]]) -> None:
        """从一行 namelist 内容中提取所有 key=value 赋值对。

        支持：
        - ``calculation = 'scf',``
        - ``nat = 2, ntyp = 1,``
        - ``celldm(1) = 10.263,``
        - ``conv_thr = 1.0d-8,``
        """
        # 用正则找所有 key=... 模式
        # 策略：找所有 key= 的位置，然后截取到下一个 key= 之间的内容作为 value
        _re_kw_pos = re.compile(r"([A-Za-z_]\w*(?:\(\d+\))?)\s*=\s*", re.IGNORECASE)

        matches = list(_re_kw_pos.finditer(line))
        for i, m in enumerate(matches):
            kw = m.group(1)
            val_start = m.end()
            if i + 1 < len(matches):
                val_end = matches[i + 1].start()
                val_raw = line[val_start:val_end].strip().rstrip(",").strip()
            else:
                val_raw = line[val_start:].strip().rstrip(",").strip()

            if kw and val_raw:
                result.append((kw, val_raw))

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
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
        cell_lines, species_lines, positions_lines = self._build_structure(intent, p)

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

    # ------------------------------------------------------------------
    # _build_structure
    # ------------------------------------------------------------------

    def _build_structure(
        self,
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
            result = self._try_load_structure_pymatgen(intent.structure_file, p)
            if result is not None:
                return result

        return self._builtin_si_structure(p)

    def _try_load_structure_pymatgen(
        self,
        structure_file: str,
        p: dict[str, Any],
    ) -> tuple[list[str], list[str], list[str]] | None:
        """尝试用 pymatgen 加载结构文件；失败时返回 None（优雅降级）。"""
        try:
            from pymatgen.core import Structure  # type: ignore

            struct = Structure.from_file(structure_file)
            cell_lines = self._cell_from_pymatgen(struct)
            species_lines = self._species_from_pymatgen(struct, p)
            positions_lines = self._positions_from_pymatgen(struct)
            return cell_lines, species_lines, positions_lines
        except Exception:  # noqa: BLE001 — 优雅降级
            return None

    def _cell_from_pymatgen(self, struct: Any) -> list[str]:
        latt = struct.lattice
        result = []
        for vec in latt.matrix:
            result.append(f"  {vec[0]:.6f}  {vec[1]:.6f}  {vec[2]:.6f}")
        return result

    def _species_from_pymatgen(self, struct: Any, p: dict[str, Any]) -> list[str]:
        elements = sorted({str(site.specie.symbol) for site in struct})
        result = []
        for elem in elements:
            mass = _element_mass(elem)
            pseudo = _default_pseudo(elem)
            result.append(f"{elem}  {mass}  {pseudo}")
        return result

    def _positions_from_pymatgen(self, struct: Any) -> list[str]:
        result = []
        for site in struct:
            fc = site.frac_coords
            result.append(
                f"{site.specie.symbol}  {fc[0]:.6f}  {fc[1]:.6f}  {fc[2]:.6f}"
            )
        return result

    def _builtin_si_structure(
        self, p: dict[str, Any]
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

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """基于 Schema 做静态校验。

        检查项：
        1. ecutwfc 范围（< 10 → warning；> 200 → warning）
        2. ecutrho 缺失时 info
        3. ecutrho/ecutwfc < 4 → warning（PAW/US 需要 ≥ 8）
        4. conv_thr > 1e-4 → warning
        5. nat 与 ATOMIC_POSITIONS 原子数不匹配 → error
        6. ntyp 与 ATOMIC_SPECIES 种类数不匹配 → error
        7. 缺少 K_POINTS → error
        8. 缺少 CELL_PARAMETERS（ibrav=0 时）→ error
        9. 枚举值检查
        10. 未知参数检查
        11. 追加 parse_errors
        """
        diags: list[Diagnostic] = []

        # ---- 收集文档中出现的参数（大写名 → param）----
        present_upper: dict[str, ParsedParam] = {}
        for param in doc.params:
            # card 数据行（_rowN）不参与参数校验
            if param.name.startswith("_"):
                continue
            present_upper[param.name.upper()] = param

        # ---- 工具函数：获取参数值（浮点）----
        def _get_float(name: str) -> float | None:
            p = present_upper.get(name.upper())
            if p is None:
                return None
            try:
                raw = str(p.value).replace("d", "e").replace("D", "e")
                return float(raw)
            except (ValueError, TypeError):
                return None

        def _get_int(name: str) -> int | None:
            p = present_upper.get(name.upper())
            if p is None:
                return None
            try:
                return int(p.value)
            except (ValueError, TypeError):
                return None

        # ---- 1. ecutwfc 范围 ----
        ecutwfc = _get_float("ecutwfc")
        if ecutwfc is not None:
            p_ecutwfc = present_upper["ECUTWFC"]
            if ecutwfc < 10:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=f"ecutwfc = {ecutwfc} Ry 过小（< 10 Ry），计算结果不可靠",
                        range=p_ecutwfc.range,
                        param="ecutwfc",
                        suggestion="NC 赝势通常 40-80 Ry；PAW/US 赝势通常 25-60 Ry",
                        rule_id="ecutwfc-too-small",
                    )
                )
            elif ecutwfc > 200:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=f"ecutwfc = {ecutwfc} Ry 过大（> 200 Ry），计算成本极高",
                        range=p_ecutwfc.range,
                        param="ecutwfc",
                        suggestion="请确认是否真的需要如此高的截断能",
                        rule_id="ecutwfc-too-large",
                    )
                )

        # ---- 2. ecutrho 缺失时 info ----
        ecutrho = _get_float("ecutrho")
        if ecutrho is None and ecutwfc is not None:
            diags.append(
                Diagnostic(
                    severity="info",
                    message=(
                        "ecutrho 未设置，默认 4×ecutwfc。"
                        "PAW/US 赝势通常需要 8-12×ecutwfc，建议明确设置。"
                    ),
                    param="ecutrho",
                    suggestion=f"对 PAW/US 赝势建议设 ecutrho = {int(8 * ecutwfc)} Ry",
                    rule_id="ecutrho-not-set",
                )
            )
        elif ecutrho is not None and ecutwfc is not None and ecutwfc > 0:
            # ---- 3. ecutrho/ecutwfc < 4 → warning ----
            ratio = ecutrho / ecutwfc
            if ratio < 4:
                p_ecutrho = present_upper["ECUTRHO"]
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=(
                            f"ecutrho/ecutwfc = {ratio:.1f} < 4，"
                            "PAW/US 赝势通常需要比值 ≥ 4（推荐 ≥ 8）"
                        ),
                        range=p_ecutrho.range,
                        param="ecutrho",
                        suggestion=f"建议 ecutrho >= {int(4 * ecutwfc)} Ry",
                        rule_id="ecutrho-ratio-low",
                    )
                )

        # ---- 4. conv_thr > 1e-4 → warning ----
        conv_thr_val = _get_float("conv_thr")
        if conv_thr_val is not None and conv_thr_val > 1e-4:
            p_conv = present_upper["CONV_THR"]
            diags.append(
                Diagnostic(
                    severity="warning",
                    message=(
                        f"conv_thr = {conv_thr_val:.2e} 较宽松（> 1e-4 Ry），"
                        "SCF 收敛精度可能不足"
                    ),
                    range=p_conv.range,
                    param="conv_thr",
                    suggestion="建议 conv_thr <= 1.0d-6（弛豫时 1.0d-8）",
                    rule_id="conv-thr-loose",
                )
            )

        # ---- 5. nat 与 ATOMIC_POSITIONS 原子数不匹配 ----
        nat_val = _get_int("nat")
        pos_sec = doc.get_section("ATOMIC_POSITIONS")
        if pos_sec is not None and nat_val is not None:
            # card 数据行：section.params 中不含 _option 的行
            atom_rows = [pm for pm in pos_sec.params if not pm.name.startswith("_")]
            if len(atom_rows) != nat_val:
                diags.append(
                    Diagnostic(
                        severity="error",
                        message=(
                            f"nat = {nat_val} 但 ATOMIC_POSITIONS 中有 {len(atom_rows)} 个原子行，"
                            "不匹配"
                        ),
                        param="nat",
                        suggestion="请确保 nat 与 ATOMIC_POSITIONS 中原子行数一致",
                        rule_id="nat-mismatch",
                    )
                )

        # ---- 6. ntyp 与 ATOMIC_SPECIES 种类数不匹配 ----
        ntyp_val = _get_int("ntyp")
        species_sec = doc.get_section("ATOMIC_SPECIES")
        if species_sec is not None and ntyp_val is not None:
            species_rows = [
                pm for pm in species_sec.params if not pm.name.startswith("_")
            ]
            if len(species_rows) != ntyp_val:
                diags.append(
                    Diagnostic(
                        severity="error",
                        message=(
                            f"ntyp = {ntyp_val} 但 ATOMIC_SPECIES 中有 {len(species_rows)} 种元素，"
                            "不匹配"
                        ),
                        param="ntyp",
                        suggestion="请确保 ntyp 与 ATOMIC_SPECIES 中元素种类数一致",
                        rule_id="ntyp-mismatch",
                    )
                )

        # ---- 7. 缺少 K_POINTS → error ----
        kpoints_sec = doc.get_section("K_POINTS")
        if kpoints_sec is None:
            diags.append(
                Diagnostic(
                    severity="error",
                    message="缺少 K_POINTS 卡片，QE pw.x 无法运行",
                    suggestion="添加 K_POINTS 卡片，例如：\nK_POINTS automatic\n  4 4 4 0 0 0",
                    rule_id="missing-kpoints",
                )
            )

        # ---- 8. 缺少 CELL_PARAMETERS（ibrav=0 时）→ error ----
        ibrav_val = _get_int("ibrav")
        if ibrav_val == 0 or ibrav_val is None:
            cell_sec = doc.get_section("CELL_PARAMETERS")
            if cell_sec is None:
                diags.append(
                    Diagnostic(
                        severity="error",
                        message="ibrav = 0 时必须提供 CELL_PARAMETERS 卡片",
                        suggestion=(
                            "添加 CELL_PARAMETERS angstrom 卡片，"
                            "例如：\nCELL_PARAMETERS angstrom\n"
                            "  0.000  2.7155  2.7155\n"
                            "  2.7155  0.000  2.7155\n"
                            "  2.7155  2.7155  0.000"
                        ),
                        rule_id="missing-cell-parameters",
                    )
                )

        # ---- 9. 枚举值检查（只对 namelist 参数，跳过 card 数据行）----
        for param in doc.params:
            # 跳过 _option 等内部参数
            if param.name.startswith("_"):
                continue
            # 跳过 card section 数据行（section_path 是纯大写的 card 名，如 ATOMIC_POSITIONS）
            if param.section_path.upper() in _CARD_NAMES:
                continue
            tag = schema.get_tag("qe", param.name)
            if tag is None:
                continue
            if tag.enum_values and isinstance(param.value, str):
                val_lo = param.value.lower()
                enum_lo = [e.lower() for e in tag.enum_values]
                if val_lo not in enum_lo:
                    diags.append(
                        Diagnostic(
                            severity="error",
                            message=(f"{param.name} = '{param.value}' 不是合法枚举值"),
                            range=param.range,
                            param=param.name,
                            suggestion=f"合法值: {', '.join(tag.enum_values)}",
                            rule_id="invalid-enum-value",
                        )
                    )

        # ---- 10. 未知参数检查（只对 namelist 参数）----
        for param in doc.params:
            # 跳过内部参数和 card 数据行
            if param.name.startswith("_"):
                continue
            if param.section_path.upper() in _CARD_NAMES:
                continue
            name_up = param.name.upper()
            # 去除下标，如 celldm(1) → celldm
            name_base = re.sub(r"\(\d+\)$", "", name_up)
            tag = schema.get_tag("qe", param.name)
            if tag is None and name_base not in _KNOWN_PARAMS:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=f"未知参数 '{param.name}'，不在 QE Schema 中",
                        range=param.range,
                        param=param.name,
                        suggestion="检查参数名拼写，或查阅 QE 手册",
                        rule_id="unknown-param",
                    )
                )

        # ---- 11. 追加 parse_errors ----
        diags.extend(doc.parse_errors)
        return diags

    # ------------------------------------------------------------------
    # get_completions
    # ------------------------------------------------------------------

    def get_completions(
        self,
        doc: DocumentModel,
        line: int,
        col: int,
        schema: SchemaRegistry,
    ) -> list[CompletionItem]:
        """返回光标位置处的补全候选列表。

        根据光标所在 section 名称过滤：
        - 在 &CONTROL 内 → 返回 section=&CONTROL 的参数
        - 在 &SYSTEM 内 → 返回 section=&SYSTEM 的参数
        - 在 &ELECTRONS 内 → 返回 section=&ELECTRONS 的参数
        - 在 &IONS 内 → 返回 section=&IONS 的参数
        - 在 &CELL 内 → 返回 section=&CELL 的参数
        - 其他位置 → 返回所有参数
        """
        current_section = self._section_name_at_line(doc, line)
        all_tags = schema.list_tags("qe")
        items: list[CompletionItem] = []

        for tag in all_tags:
            # 确定优先级
            if current_section and tag.section:
                if tag.section.upper() == current_section.upper():
                    priority = 0
                else:
                    priority = 10
            else:
                priority = 5

            # 若能确定 section，只高优先级返回属于该 section 的参数
            if current_section and tag.section and priority == 10:
                continue  # 在已知 section 内不显示其他 section 的参数

            items.append(
                CompletionItem(
                    label=tag.name,
                    detail=tag.to_completion_detail(),
                    documentation=tag.to_markdown(),
                    insert_text=(
                        f"{tag.name} = {tag.default},"
                        if tag.default is not None
                        else f"{tag.name} = ,"
                    ),
                    category=tag.category,
                    sort_priority=priority,
                )
            )

        items.sort(key=lambda x: (x.sort_priority, x.label.lower()))
        return items

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _section_name_at_line(self, doc: DocumentModel, line: int) -> str:
        """返回覆盖指定行号（1-based）的 section 名称；找不到返回空字符串。"""
        for sec in doc.sections:
            if sec.range.start_line <= line <= sec.range.end_line:
                return sec.name
        return ""


# ---------------------------------------------------------------------------
# 工具函数
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


def _element_mass(elem: str) -> float:
    """返回元素原子质量；未知元素返回 1.0。"""
    return _ELEMENT_MASSES.get(elem, 1.0)


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


def _default_pseudo(elem: str) -> str:
    """返回元素的默认赝势文件名；未知元素使用通用命名约定。"""
    if elem in _PSEUDO_MAP:
        return _PSEUDO_MAP[elem]
    # 通用命名约定（SSSP 风格）
    return f"{elem}.pbe-n-kjpaw_psl.1.0.0.UPF"
