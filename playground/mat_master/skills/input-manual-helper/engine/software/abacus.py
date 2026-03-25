"""
abacus.py — ABACUS 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 ABACUS INPUT 文件（扁平 key value 格式）
  - render:          生成可运行的 ABACUS INPUT + STRU + KPT 三文件模板
  - get_diagnostics: 基于 Schema 的静态校验 + ABACUS 特有物理规则
  - get_completions: 按关键字上下文返回参数建议

ABACUS 输入格式概述
--------------------
ABACUS 使用三个输入文件：
  INPUT   — 计算参数（key value 格式，#注释，行内不区分大小写键名）
  STRU    — 晶体结构（元素、赝势、轨道、晶格、原子坐标）
  KPT     — k 点采样（Monkhorst-Pack 或高对称路径）

INPUT 示例::

    INPUT_PARAMETERS
    suffix          ABACUS
    ntype           2
    calculation     scf
    ecutwfc         50
    basis_type      lcao
    pseudo_dir      ./
    orbital_dir     ./
    scf_thr         1e-7
    scf_nmax        100
    smearing_method gauss
    smearing_sigma  0.015
    cal_force       1
    cal_stress      0
    out_chg         0
    out_band        0
    out_dos         0
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

# KPT 模板（Monkhorst-Pack）
_SCF_KPT = """\
K_POINTS
0
Gamma
4 4 4 0 0 0
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
_KNOWN_PARAMS: frozenset[str] = frozenset({
    # system
    "suffix", "ntype", "calculation", "esolver_type", "pseudo_dir", "orbital_dir",
    "stru_file", "kpoint_file", "symmetry", "vdw_method", "vdw_s6",
    # electronic
    "ecutwfc", "basis_type", "nspin", "nbands", "dft_functional",
    "smearing_method", "smearing_sigma", "gamma_only", "kspacing",
    "noncolin", "lspinorb", "ks_solver", "pw_diag_thr", "pw_diag_nmax",
    "npool", "nband_istate", "mem_saver", "lda_plus_u", "hubbard_u",
    "orbital_corr", "nupdown", "sc_mag_switch",
    # scf
    "scf_thr", "scf_nmax", "mixing_type", "mixing_beta", "mixing_ndim",
    "init_chg", "scf_os_ndim",
    # ionic / relax
    "force_thr", "stress_thr", "relax_nmax", "relax_method",
    "cal_force", "cal_stress", "fixed_atoms",
    # md
    "md_type", "md_nstep", "md_dt", "md_tfirst", "md_tlast",
    "md_tfreq", "md_dumpfreq", "md_restartfreq", "init_vel",
    # output
    "out_chg", "out_dos", "out_band", "out_proj_band", "out_stru",
    "out_wfc_lcao", "out_dipole", "out_mul", "out_allband",
    "dos_sigma", "dos_emin_ev", "dos_emax_ev", "dos_edelta_ev",
    "printe", "berry_phase", "gdir", "towannier90", "wannier_spin",
    "cal_cond",
    # deepks
    "deepks_out_labels", "deepks_scf",
    # slab / field
    "dip_cor_flag", "dip_cor_axis", "efield_flag", "efield_dir", "efield_amp",
})

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
    return SourceRange(start_line=line, start_col=col_start,
                       end_line=line, end_col=col_end)


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


class AbacusBackend(SoftwareBackend):
    """ABACUS INPUT 文件后端。

    ABACUS INPUT 使用扁平 key-value 格式：
      - 第一行必须是 ``INPUT_PARAMETERS``
      - 后续每行格式为 ``keyword  value  # optional comment``
      - 关键字大小写不敏感
      - 注释以 ``#`` 开始，可出现在行尾或行首

    注意：ABACUS 的完整输入包含三个文件（INPUT, STRU, KPT）。
    本后端仅解析/生成 INPUT 文件；STRU 和 KPT 通过 ``render`` 的
    附加输出（多文件模式）提供。
    """

    software_name = "abacus"

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """将 ABACUS INPUT 文本解析为 DocumentModel。"""
        lines = text.splitlines()
        params: list[ParsedParam] = []

        in_header = False
        for lineno, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if _is_blank_or_comment(stripped):
                continue

            # 检查 INPUT_PARAMETERS 标头
            if _HEADER_RE.match(stripped):
                in_header = True
                continue

            if not in_header:
                # 如果第一个非空行不是 INPUT_PARAMETERS，也允许解析（宽容模式）
                in_header = True

            # 去掉行内注释
            effective = _strip_comment(raw_line).strip()
            if not effective:
                continue

            # 按空白拆分：keyword + 剩余部分作为 value
            parts = effective.split(None, 1)
            if len(parts) == 0:
                continue
            keyword = parts[0].lower()
            raw_value = parts[1].strip() if len(parts) > 1 else ""
            value = _parse_abacus_value(raw_value) if raw_value else ""

            col_end = len(effective)
            src_range = _make_range(lineno, 0, col_end)
            params.append(
                ParsedParam(
                    name=keyword,
                    value=value,
                    raw_text=raw_line,
                    range=src_range,
                    section_path="INPUT_PARAMETERS",
                )
            )

        # DocumentModel uses a flat namespace; wrap in a single section
        section = ParsedSection(
            name="INPUT_PARAMETERS",
            range=_make_range(1, 0, 0),
            params=params,
        )
        doc = DocumentModel(
            software="abacus",
            source=source,
            raw_text=text,
            sections=[section],
            params=params,
        )
        return doc

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
        """生成 ABACUS INPUT 文件内容。

        对于 ABACUS，返回的字符串是 INPUT 文件的内容。
        调用方可通过 render_abacus_all() 同时获得 STRU 和 KPT。
        """
        task = (intent.task_type or "scf").lower().strip()
        overrides = dict(intent.params or {})

        # 基础参数
        params: dict[str, Any] = {
            "suffix": "ABACUS",
            "ntype": 1,
            "calculation": "scf",
            "ecutwfc": 50,
            "basis_type": "lcao",
            "pseudo_dir": "./",
            "orbital_dir": "./",
            "scf_thr": "1e-7",
            "scf_nmax": 100,
            "smearing_method": "gauss",
            "smearing_sigma": 0.015,
            "cal_force": 0,
            "cal_stress": 0,
            "out_chg": 0,
            "out_band": 0,
            "out_dos": 0,
        }

        # 任务类型定制
        if task in ("relax", "cell-relax"):
            params.update({
                "calculation": task,
                "cal_force": 1,
                "cal_stress": 1,
                "force_thr": "1e-3",
                "relax_nmax": 100,
                "relax_method": "bfgs",
                "out_stru": 1,
            })
        elif task == "band":
            params.update({
                "calculation": "nscf",
                "nbands": 20,
                "out_band": 1,
            })
        elif task == "dos":
            params.update({
                "calculation": "nscf",
                "nbands": 20,
                "out_dos": 1,
                "dos_sigma": 0.07,
                "dos_edelta_ev": 0.01,
            })
        elif task in ("md", "nvt", "npt"):
            params.update({
                "calculation": "md",
                "md_type": "nvt",
                "md_nstep": 1000,
                "md_dt": 1.0,
                "md_tfirst": 300,
                "md_dumpfreq": 10,
                "md_restartfreq": 100,
                "cal_force": 1,
                "init_vel": 0,
            })
        elif task == "cell-relax":
            params.update({
                "calculation": "cell-relax",
                "cal_force": 1,
                "cal_stress": 1,
                "stress_thr": 10.0,
            })

        # 覆盖用户指定参数
        for k, v in overrides.items():
            params[k.lower()] = v

        return self._format_input(params)

    def _format_input(self, params: dict[str, Any]) -> str:
        """将参数 dict 格式化为 ABACUS INPUT 字符串。"""
        lines = ["INPUT_PARAMETERS"]
        # 按关键字名称的字典序分组输出（维持可预测顺序）
        category_order = [
            # system
            ["suffix", "ntype", "calculation", "esolver_type",
             "pseudo_dir", "orbital_dir", "stru_file", "kpoint_file", "symmetry"],
            # electronic structure
            ["ecutwfc", "basis_type", "nspin", "nbands", "dft_functional",
             "gamma_only", "kspacing", "smearing_method", "smearing_sigma",
             "ks_solver", "pw_diag_thr", "pw_diag_nmax", "npool",
             "noncolin", "lspinorb", "lda_plus_u", "hubbard_u", "orbital_corr",
             "nupdown", "vdw_method", "vdw_s6"],
            # scf
            ["scf_thr", "scf_nmax", "mixing_type", "mixing_beta", "mixing_ndim",
             "init_chg"],
            # ionic / relax
            ["cal_force", "cal_stress", "force_thr", "stress_thr",
             "relax_nmax", "relax_method", "fixed_atoms"],
            # md
            ["md_type", "md_nstep", "md_dt", "md_tfirst", "md_tlast",
             "md_tfreq", "md_dumpfreq", "md_restartfreq", "init_vel"],
            # output
            ["out_chg", "out_dos", "out_band", "out_proj_band", "out_stru",
             "out_wfc_lcao", "out_dipole", "out_mul", "out_allband",
             "dos_sigma", "dos_emin_ev", "dos_emax_ev", "dos_edelta_ev",
             "berry_phase", "gdir", "towannier90", "wannier_spin",
             "deepks_out_labels", "deepks_scf", "cal_cond",
             "dip_cor_flag", "dip_cor_axis",
             "efield_flag", "efield_dir", "efield_amp"],
        ]

        emitted: set[str] = set()
        for group in category_order:
            group_lines = []
            for key in group:
                if key in params:
                    val = params[key]
                    group_lines.append(f"{key:<24}{val}")
                    emitted.add(key)
            if group_lines:
                lines.append("")
                lines.extend(group_lines)

        # 剩余用户提供的参数（未在预定义顺序中）
        extras = [(k, v) for k, v in params.items() if k not in emitted]
        if extras:
            lines.append("")
            for k, v in extras:
                lines.append(f"{k:<24}{v}")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # render_all  (multi-file helper)
    # ------------------------------------------------------------------

    def render_all(self, intent: RenderIntent) -> dict[str, str]:
        """生成 ABACUS 所需的全部文件，返回 {filename: content} 字典。

        返回键：'INPUT', 'STRU', 'KPT'。
        如果 intent.structure_file 已提供则 STRU 包含提示注释；
        否则使用内建 Si 金刚石结构作为占位符。
        """
        task = (intent.task_type or "scf").lower().strip()
        input_text = self.render(intent)

        # STRU 占位符
        if intent.structure_file:
            stru = (
                f"# Structure loaded from: {intent.structure_file}\n"
                "# Replace this block with the actual ABACUS STRU content.\n"
                + _SI_STRU
            )
        else:
            stru = (
                "# Placeholder: Si diamond cubic cell (a = 5.431 Å)\n"
                "# Replace with your actual structure.\n"
                + _SI_STRU
            )

        # KPT 占位符
        if task in ("band",):
            kpt = _BAND_KPT
        else:
            kpt = _SCF_KPT

        return {"INPUT": input_text, "STRU": stru, "KPT": kpt}

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """基于 Schema 做静态校验 + ABACUS 特有物理规则。"""
        diags: list[Diagnostic] = []

        # 收集 INPUT_PARAMETERS section 的所有参数
        all_params: list[ParsedParam] = []
        for section in doc.sections:
            all_params.extend(section.params)

        present: dict[str, ParsedParam] = {p.name.lower(): p for p in all_params}

        # ------------------------------------------------------------------
        # 辅助函数
        # ------------------------------------------------------------------

        def _get_float(name: str) -> float | None:
            p = present.get(name.lower())
            if p is None:
                return None
            try:
                return float(p.value)
            except (TypeError, ValueError):
                return None

        def _get_int(name: str) -> int | None:
            p = present.get(name.lower())
            if p is None:
                return None
            try:
                return int(str(p.value).split(".")[0])
            except (TypeError, ValueError):
                return None

        def _get_str(name: str) -> str | None:
            p = present.get(name.lower())
            if p is None:
                return None
            return str(p.value).lower().strip()

        # ------------------------------------------------------------------
        # Rule 1: unknown parameters
        # ------------------------------------------------------------------
        for p in all_params:
            if p.name.lower() not in _KNOWN_PARAMS:
                tag = schema.get_tag("abacus", p.name)
                if tag is None:
                    diags.append(Diagnostic(
                        severity="warning",
                        message=f"Unknown ABACUS parameter '{p.name}'. Check spelling or ABACUS version.",
                        range=_make_range(_line(p), 0, 0),
                        param=p.name,
                    ))

        # ------------------------------------------------------------------
        # Rule 2: ecutwfc range check
        # ------------------------------------------------------------------
        ecutwfc = _get_float("ecutwfc")
        if ecutwfc is not None:
            if ecutwfc < 10:
                p = present["ecutwfc"]
                diags.append(Diagnostic(
                    severity="error",
                    message=f"ecutwfc = {ecutwfc} Ry is extremely low. Minimum recommended: 30 Ry.",
                    range=_make_range(_line(p), 0, 0),
                    param="ecutwfc",
                    suggestion="Increase ecutwfc to at least 50 Ry for norm-conserving pseudopotentials.",
                ))
            elif ecutwfc < 30:
                p = present["ecutwfc"]
                diags.append(Diagnostic(
                    severity="warning",
                    message=f"ecutwfc = {ecutwfc} Ry may be insufficient. Consider 50+ Ry.",
                    range=_make_range(_line(p), 0, 0),
                    param="ecutwfc",
                ))

        # ------------------------------------------------------------------
        # Rule 3: calculation type validation
        # ------------------------------------------------------------------
        calc = _get_str("calculation")
        valid_calcs = {"scf", "relax", "cell-relax", "md", "nscf", "get_s"}
        if calc and calc not in valid_calcs:
            p = present["calculation"]
            diags.append(Diagnostic(
                severity="error",
                message=f"Unknown calculation type '{calc}'. Valid: {', '.join(sorted(valid_calcs))}.",
                range=_make_range(_line(p), 0, 0),
                param="calculation",
            ))

        # ------------------------------------------------------------------
        # Rule 4: basis_type=lcao requires orbital_dir
        # ------------------------------------------------------------------
        basis = _get_str("basis_type")
        if basis == "lcao" and "orbital_dir" not in present:
            diags.append(Diagnostic(
                severity="warning",
                message="basis_type=lcao requires 'orbital_dir' to be set (path to .orb files).",
                param="orbital_dir",
                suggestion="Add: orbital_dir  /path/to/orbital/files",
            ))

        # ------------------------------------------------------------------
        # Rule 5: relax without cal_force
        # ------------------------------------------------------------------
        if calc in ("relax", "cell-relax"):
            cal_force = _get_int("cal_force")
            if cal_force is not None and cal_force == 0:
                p = present.get("cal_force")
                _ln = _line(p) if p else 0
                diags.append(Diagnostic(
                    severity="error",
                    message="cal_force=0 but calculation='relax'/'cell-relax' requires forces. Set cal_force=1.",
                    range=_make_range(_ln, 0, 0),
                    param="cal_force",
                ))
            if calc == "cell-relax":
                cal_stress = _get_int("cal_stress")
                if cal_stress is not None and cal_stress == 0:
                    p = present.get("cal_stress")
                    _ln = _line(p) if p else 0
                    diags.append(Diagnostic(
                        severity="error",
                        message="cal_stress=0 but calculation='cell-relax' requires stress. Set cal_stress=1.",
                        range=_make_range(_ln, 0, 0),
                        param="cal_stress",
                    ))

        # ------------------------------------------------------------------
        # Rule 6: nspin consistency
        # ------------------------------------------------------------------
        nspin = _get_int("nspin")
        noncolin = _get_int("noncolin")
        lspinorb = _get_int("lspinorb")

        if noncolin == 1 and nspin != 4:
            p = present.get("noncolin")
            _ln = _line(p) if p else 0
            diags.append(Diagnostic(
                severity="error",
                message="noncolin=1 requires nspin=4.",
                range=_make_range(_ln, 0, 0),
                param="noncolin",
                suggestion="Add: nspin  4",
            ))

        if lspinorb == 1 and noncolin != 1:
            p = present.get("lspinorb")
            _ln = _line(p) if p else 0
            diags.append(Diagnostic(
                severity="error",
                message="lspinorb=1 requires noncolin=1 and nspin=4.",
                range=_make_range(_ln, 0, 0),
                param="lspinorb",
            ))

        # ------------------------------------------------------------------
        # Rule 7: smearing_method=fixed inconsistency for metals
        # ------------------------------------------------------------------
        smearing = _get_str("smearing_method")
        smearing_sigma = _get_float("smearing_sigma")
        if smearing and smearing != "fixed" and smearing_sigma is None:
            diags.append(Diagnostic(
                severity="warning",
                message=f"smearing_method='{smearing}' specified but smearing_sigma is not set. Default 0.015 Ry will be used.",
                param="smearing_sigma",
                suggestion="Add: smearing_sigma  0.015",
            ))

        # ------------------------------------------------------------------
        # Rule 8: MD requires cal_force=1
        # ------------------------------------------------------------------
        if calc == "md":
            cal_force = _get_int("cal_force")
            if cal_force is not None and cal_force == 0:
                p = present.get("cal_force")
                _ln = _line(p) if p else 0
                diags.append(Diagnostic(
                    severity="error",
                    message="cal_force=0 but calculation='md' requires forces. Set cal_force=1.",
                    range=_make_range(_ln, 0, 0),
                    param="cal_force",
                ))
            md_dt = _get_float("md_dt")
            if md_dt is not None and md_dt > 5.0:
                p = present["md_dt"]
                diags.append(Diagnostic(
                    severity="warning",
                    message=f"md_dt = {md_dt} fs is large. Values > 3 fs may cause energy drift.",
                    range=_make_range(_line(p), 0, 0),
                    param="md_dt",
                ))

        # ------------------------------------------------------------------
        # Rule 9: band calculation should set nbands
        # ------------------------------------------------------------------
        if calc == "nscf" and "nbands" not in present:
            out_band = _get_int("out_band")
            out_dos = _get_int("out_dos")
            if out_band or out_dos:
                diags.append(Diagnostic(
                    severity="warning",
                    message="nscf calculation with out_band/out_dos: consider setting nbands explicitly to include empty states.",
                    param="nbands",
                    suggestion="Add: nbands  <number of bands including empty states>",
                ))

        # ------------------------------------------------------------------
        # Rule 10: DFT+U consistency
        # ------------------------------------------------------------------
        lda_plus_u = _get_int("lda_plus_u")
        if lda_plus_u == 1:
            if "hubbard_u" not in present:
                diags.append(Diagnostic(
                    severity="error",
                    message="lda_plus_u=1 requires 'hubbard_u' to list U values (in eV) for each atom type.",
                    param="hubbard_u",
                ))
            if "orbital_corr" not in present:
                diags.append(Diagnostic(
                    severity="error",
                    message="lda_plus_u=1 requires 'orbital_corr' to specify the correlated l for each atom type.",
                    param="orbital_corr",
                ))

        # ------------------------------------------------------------------
        # Rule 11: mixing_beta range
        # ------------------------------------------------------------------
        mb = _get_float("mixing_beta")
        if mb is not None:
            if mb <= 0 or mb > 1:
                p = present["mixing_beta"]
                diags.append(Diagnostic(
                    severity="error",
                    message=f"mixing_beta = {mb} is outside (0, 1]. Typical values: 0.1–0.8.",
                    range=_make_range(_line(p), 0, 0),
                    param="mixing_beta",
                ))

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
        """在光标位置返回补全候选。"""
        raw_lines = (doc.raw_text or "").splitlines()
        current_line = raw_lines[line - 1] if 0 < line <= len(raw_lines) else ""
        prefix = _strip_comment(current_line[:col]).strip().split()[-1:][0].lower() if current_line[:col].strip() else ""

        items: list[CompletionItem] = []
        tags = schema.list_tags("abacus")
        for tag in tags:
            name_lower = tag.name.lower()
            if prefix and not name_lower.startswith(prefix):
                continue
            snippet = f"{tag.name}  "
            if tag.default is not None:
                snippet += str(tag.default)
            items.append(
                CompletionItem(
                    label=tag.name,
                    detail=f"({tag.param_type}) {tag.category}",
                    documentation=tag.description[:120] + ("..." if len(tag.description) > 120 else ""),
                    insert_text=snippet,
                    category=tag.category,
                    sort_priority=1 if tag.category in ("system", "electronic", "scf") else 2,
                )
            )
        items.sort(key=lambda x: (x.sort_priority, x.label))
        return items


# ---------------------------------------------------------------------------
# Standalone helpers (used by render scripts)
# ---------------------------------------------------------------------------

def render_abacus_files(intent: RenderIntent) -> dict[str, str]:
    """Public helper: generate all ABACUS input files as a dict."""
    backend = AbacusBackend()
    return backend.render_all(intent)
