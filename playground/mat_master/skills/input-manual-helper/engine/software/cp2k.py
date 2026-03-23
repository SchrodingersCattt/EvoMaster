"""
cp2k.py — CP2K 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 &SECTION ... &END SECTION 嵌套结构
  - render:          生成可运行的 CP2K 输入文件（内建 Si 金刚石结构）
  - get_diagnostics: 基于 Schema 的静态校验
  - get_completions: 按当前 section 路径返回可用参数建议
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

# 已知参数所属 section（用于 diagnostics unknown-param 检查豁免）
_KNOWN_SECTION_KEYWORDS: set[str] = {
    # &GLOBAL
    "PROJECT", "RUN_TYPE", "PRINT_LEVEL", "WALLTIME", "SEED", "PREFERRED_DIAG_LIBRARY",
    # &FORCE_EVAL
    "METHOD",
    # &FORCE_EVAL/&DFT
    "BASIS_SET_FILE_NAME", "POTENTIAL_FILE_NAME", "CHARGE", "MULTIPLICITY", "UKS",
    # &FORCE_EVAL/&DFT/&MGRID
    "CUTOFF", "REL_CUTOFF", "NGRIDS",
    # &FORCE_EVAL/&DFT/&QS
    "EPS_DEFAULT", "EPS_FILTER_MATRIX", "EXTRAPOLATION",
    # &FORCE_EVAL/&DFT/&SCF
    "SCF_GUESS", "EPS_SCF", "MAX_SCF", "MAX_DIIS", "ADDED_MOS",
    "EPS_SCF_HISTORY", "MAX_SCF_HISTORY",
    # &FORCE_EVAL/&DFT/&SCF/&MIXING
    "ALPHA", "NBUFFER",
    # &FORCE_EVAL/&DFT/&SCF/&SMEAR
    "ELECTRONIC_TEMPERATURE",
    # &FORCE_EVAL/&DFT/&XC/&XC_FUNCTIONAL
    "PBE", "LDA", "BLYP", "PBE0", "B3LYP",
    # &FORCE_EVAL/&DFT/&KPOINTS
    "SCHEME", "SYMMETRY", "WAVEFUNCTIONS",
    # &FORCE_EVAL/&SUBSYS/&CELL
    "ABC", "ALPHA_BETA_GAMMA", "A", "B", "C", "PERIODIC",
    # &FORCE_EVAL/&SUBSYS/&COORD
    "UNIT",
    # &FORCE_EVAL/&SUBSYS/&KIND
    "BASIS_SET", "POTENTIAL", "ELEMENT", "MASS",
    # &MOTION/&GEO_OPT
    "OPTIMIZER", "MAX_ITER", "MAX_FORCE", "RMS_FORCE", "MAX_DR", "RMS_DR",
    # &MOTION/&MD
    "ENSEMBLE", "STEPS", "TIMESTEP", "TEMPERATURE", "COMVEL_TOL",
    # &PRINT
    "FILENAME", "LOG_PRINT_KEY",
}

# &COORD section 内的行是原子坐标（元素名 x y z），不应被当作 keyword 校验
# 当前 section path 包含 &COORD 时，跳过 unknown-param 检查
_COORD_SECTION_SUFFIX = "&COORD"

# schema 中 METHOD 的 section 是 &FORCE_EVAL/&DFT/&QS，
# 但 &FORCE_EVAL 下的 METHOD（如 Quickstep）是不同的关键字，
# 需要在 diagnostics 时区分 section 路径
_METHOD_QS_SECTION = "&FORCE_EVAL/&DFT/&QS"


# ---------------------------------------------------------------------------
# 解析器辅助
# ---------------------------------------------------------------------------

def _parse_value(raw: str) -> Any:
    """尝试将字符串转换为 Python 原生类型。"""
    raw = raw.strip()
    # 布尔
    if raw.upper() in ("TRUE", ".TRUE.", "T", "YES"):
        return True
    if raw.upper() in ("FALSE", ".FALSE.", "F", "NO"):
        return False
    # 整数
    try:
        return int(raw)
    except ValueError:
        pass
    # 浮点（含科学计数法）
    try:
        return float(raw)
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


# ---------------------------------------------------------------------------
# CP2K 后端
# ---------------------------------------------------------------------------

class CP2KBackend(SoftwareBackend):
    """CP2K 输入文件后端。

    完整实现 parse / render / get_diagnostics / get_completions。
    """

    software_name = "cp2k"

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """解析 CP2K 输入文件的 &SECTION ... &END SECTION 嵌套结构。

        - 识别 ``&SECTION_NAME [args]`` 开始和 ``&END [SECTION_NAME]`` 结束
        - section 内的 ``KEYWORD value`` 行 → :class:`ParsedParam`
        - 注释行（``!`` 或 ``#`` 开头）跳过
        - 构建嵌套 :class:`ParsedSection` 树，生成扁平 params 列表
        """
        doc = DocumentModel(
            software="cp2k",
            source=source,
            raw_text=text,
        )

        lines = text.splitlines()
        # 用一个"虚根" section 作为栈底，方便统一处理
        root = ParsedSection(
            name="",
            range=SourceRange(1, 0, len(lines), 0),
        )
        stack: list[ParsedSection] = [root]
        flat_params: list[ParsedParam] = []

        _re_section_start = re.compile(r"^&([A-Za-z_][A-Za-z0-9_]*)(.*)$")
        _re_section_end = re.compile(r"^&END\s*([A-Za-z_][A-Za-z0-9_]*)?", re.IGNORECASE)
        _re_keyword = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.*)", re.DOTALL)

        for lineno, raw_line in enumerate(lines, start=1):
            # 去掉行内注释
            line = raw_line
            # 剥离 ! 及 # 注释
            for comment_char in ("!", "#"):
                idx = line.find(comment_char)
                if idx >= 0:
                    line = line[:idx]
            line = line.strip()

            if not line:
                continue

            # ---- &END ----
            m_end = _re_section_end.match(line)
            if m_end:
                if len(stack) > 1:
                    closed = stack.pop()
                    closed.range.end_line = lineno
                    closed.range.end_col = len(raw_line)
                else:
                    doc.parse_errors.append(
                        Diagnostic(
                            severity="error",
                            message=f"意外的 &END（无匹配的 &SECTION）",
                            range=_make_range(lineno, 0, len(raw_line)),
                            rule_id="unexpected-end",
                        )
                    )
                continue

            # ---- &SECTION_START ----
            m_start = _re_section_start.match(line)
            if m_start:
                sec_name = "&" + m_start.group(1).upper()
                current = stack[-1]
                new_sec = ParsedSection(
                    name=sec_name,
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                    parent=current,
                )
                current.children.append(new_sec)
                stack.append(new_sec)
                continue

            # ---- KEYWORD value ----
            m_kw = _re_keyword.match(line)
            if m_kw:
                kw_name = m_kw.group(1).upper()
                kw_raw = m_kw.group(2).strip()
                kw_val = _parse_value(kw_raw)
                current = stack[-1]
                # 计算 section_path（跳过虚根）
                sec_path = current.path if current.name else ""
                param = ParsedParam(
                    name=kw_name,
                    value=kw_val,
                    raw_text=raw_line,
                    range=_make_range(lineno, 0, len(raw_line)),
                    section_path=sec_path,
                )
                current.params.append(param)
                flat_params.append(param)
                continue
            # 无法识别的行 → 忽略（可能是续行、特殊语法）

        # 关闭未关闭的 section
        while len(stack) > 1:
            unclosed = stack.pop()
            doc.parse_errors.append(
                Diagnostic(
                    severity="warning",
                    message=f"Section {unclosed.name} 未正常关闭（缺少 &END）",
                    range=_make_range(unclosed.range.start_line, 0, 0),
                    rule_id="unclosed-section",
                )
            )

        # 顶层 sections 来自虚根的 children
        doc.sections = root.children
        doc.params = flat_params
        return doc

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
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
        cell_lines, coord_lines, kind_lines = self._build_structure(
            intent, p
        )

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
        lines += ["    &CELL"] + [f"      {l}" for l in cell_lines] + ["    &END CELL"]
        lines += ["    &COORD"] + [f"      {l}" for l in coord_lines] + ["    &END COORD"]
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

    # ------------------------------------------------------------------
    # _build_structure
    # ------------------------------------------------------------------

    def _build_structure(
        self,
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
            result = self._try_load_structure_pymatgen(intent.structure_file, p)
            if result is not None:
                return result

        return self._builtin_si_structure(p)

    def _try_load_structure_pymatgen(
        self,
        structure_file: str,
        p: dict[str, Any],
    ) -> tuple[list[str], list[str], list[tuple[str, list[str]]]] | None:
        """尝试用 pymatgen 加载结构文件；失败时返回 None（优雅降级）。"""
        try:
            from pymatgen.core import Structure  # type: ignore

            struct = Structure.from_file(structure_file)
            cell_lines = self._cell_from_pymatgen(struct)
            coord_lines = self._coord_from_pymatgen(struct)
            kind_lines = self._kind_from_pymatgen(struct, p)
            return cell_lines, coord_lines, kind_lines
        except Exception:  # noqa: BLE001 — 优雅降级
            return None

    def _cell_from_pymatgen(self, struct: Any) -> list[str]:
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

    def _coord_from_pymatgen(self, struct: Any) -> list[str]:
        lines = []
        for site in struct:
            x, y, z = site.coords
            lines.append(f"{site.specie.symbol}  {x:.6f}  {y:.6f}  {z:.6f}")
        return lines

    def _kind_from_pymatgen(
        self, struct: Any, p: dict[str, Any]
    ) -> list[tuple[str, list[str]]]:
        elements = sorted({str(site.specie.symbol) for site in struct})
        result: list[tuple[str, list[str]]] = []
        for elem in elements:
            basis, potential = _default_basis_potential(elem, p)
            result.append((elem, [f"BASIS_SET {basis}", f"POTENTIAL {potential}"]))
        return result

    def _builtin_si_structure(
        self, p: dict[str, Any]
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
        coord_lines = [
            f"{elem}  {x:.6f}  {y:.6f}  {z:.6f}"
            for elem, x, y, z in _SI_COORDS
        ]
        si_basis = _str(p.get("SI_BASIS_SET", "DZVP-MOLOPT-SR-GTH"))
        si_potential = _str(p.get("SI_POTENTIAL", "GTH-PBE-q4"))
        kind_lines: list[tuple[str, list[str]]] = [
            ("Si", [f"BASIS_SET {si_basis}", f"POTENTIAL {si_potential}"]),
        ]
        return cell_lines, coord_lines, kind_lines

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """基于 Schema 做静态校验。

        检查项：
        1. 未知参数（不在 Schema 且不在已知 section 关键字列表中）→ warning
        2. 数值范围检查（如 CUTOFF < 50 → error）
        3. 依赖检查（使用了 hybrid functional 但无 HF section → warning）
        4. 缺失建议参数（如无 CUTOFF → info）
        """
        diags: list[Diagnostic] = []

        # 收集文档中出现的参数名（大写）
        present: set[str] = {p.name.upper() for p in doc.params}

        for param in doc.params:
            name_up = param.name.upper()

            # &COORD section 内的行是原子坐标（元素名 x y z），跳过所有校验
            if param.section_path.upper().endswith(_COORD_SECTION_SUFFIX):
                continue

            tag = schema.get_tag("cp2k", param.name)

            # METHOD 在 &FORCE_EVAL 直属时值为 Quickstep，
            # 而 schema 的 METHOD tag 来自 &QS；section 不匹配则跳过枚举校验
            if name_up == "METHOD" and tag is not None:
                tag_sec_leaf = (tag.section or "").upper().split("/")[-1]
                param_sec_leaf = param.section_path.upper().split("/")[-1] if param.section_path else ""
                if tag_sec_leaf and param_sec_leaf != tag_sec_leaf:
                    continue

            # 1. 未知参数
            if tag is None and name_up not in _KNOWN_SECTION_KEYWORDS:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=f"未知参数 '{param.name}'，不在 CP2K Schema 中",
                        range=param.range,
                        param=param.name,
                        suggestion="检查参数名拼写，或查阅 CP2K 手册",
                        rule_id="unknown-param",
                    )
                )
                continue

            if tag is None:
                continue

            # 2. 数值范围检查
            if tag.valid_range is not None:
                lo, hi = tag.valid_range
                try:
                    num = float(param.value)  # type: ignore[arg-type]
                    if lo is not None and num < lo:
                        diags.append(
                            Diagnostic(
                                severity="error",
                                message=(
                                    f"{param.name} = {param.value} 低于最小值 {lo}"
                                    + (f" {tag.unit}" if tag.unit else "")
                                ),
                                range=param.range,
                                param=param.name,
                                suggestion=f"建议 {param.name} >= {lo}",
                                rule_id="value-out-of-range",
                            )
                        )
                    elif hi is not None and num > hi:
                        diags.append(
                            Diagnostic(
                                severity="error",
                                message=(
                                    f"{param.name} = {param.value} 超出最大值 {hi}"
                                    + (f" {tag.unit}" if tag.unit else "")
                                ),
                                range=param.range,
                                param=param.name,
                                suggestion=f"建议 {param.name} <= {hi}",
                                rule_id="value-out-of-range",
                            )
                        )
                except (ValueError, TypeError):
                    pass  # 非数值参数，跳过范围检查

            # 3. 枚举值检查
            if tag.enum_values and isinstance(param.value, str):
                val_up = param.value.upper()
                enum_up = [e.upper() for e in tag.enum_values]
                if val_up not in enum_up:
                    diags.append(
                        Diagnostic(
                            severity="error",
                            message=(
                                f"{param.name} = '{param.value}' 不是合法枚举值"
                            ),
                            range=param.range,
                            param=param.name,
                            suggestion=f"合法值: {', '.join(tag.enum_values)}",
                            rule_id="invalid-enum-value",
                        )
                    )

        # 3b. 依赖检查：hybrid functional → 需要 &HF section
        hybrid_keywords = {"PBE0", "B3LYP", "HSE06"}
        for param in doc.params:
            if param.name.upper() in hybrid_keywords and param.value not in (False, None):
                hf_sec = doc.get_section("&FORCE_EVAL/&DFT/&XC/&HF")
                if hf_sec is None:
                    diags.append(
                        Diagnostic(
                            severity="warning",
                            message=(
                                f"使用 hybrid functional {param.name} 时，"
                                "通常需要在 &XC 下添加 &HF section 配置精确交换"
                            ),
                            range=param.range,
                            param=param.name,
                            suggestion="在 &XC 下添加 &HF section",
                            rule_id="missing-hf-section",
                        )
                    )

        # 4. 缺失建议参数（info 级别）
        suggested_present = {
            "CUTOFF": ("CUTOFF 未设置，将使用 CP2K 默认值（通常较小），建议明确设置（如 300 Ry）", "info"),
            "EPS_SCF": ("EPS_SCF 未设置，建议明确指定收敛阈值", "info"),
        }
        for kw, (msg, sev) in suggested_present.items():
            if kw not in present:
                diags.append(
                    Diagnostic(
                        severity=sev,
                        message=msg,
                        param=kw,
                        suggestion=f"在对应 section 中添加 {kw}",
                        rule_id="missing-recommended-param",
                    )
                )

        # 追加解析阶段的错误
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

        根据光标所在行确定当前 section 路径，
        然后返回 Schema 中属于该 section（或所有 section）的参数作为候选。
        """
        # 确定当前 section 路径
        current_section_path = self._section_path_at_line(doc, line)

        all_tags = schema.list_tags("cp2k")
        items: list[CompletionItem] = []

        for tag in all_tags:
            # 若能确定 section，只返回属于该 section 的参数；否则返回所有
            if current_section_path and tag.section:
                # section 路径匹配：tag.section 应包含在当前路径中
                if not tag.section.lower().endswith(
                    current_section_path.lower().split("/")[-1]
                ):
                    continue

            items.append(
                CompletionItem(
                    label=tag.name,
                    detail=tag.to_completion_detail(),
                    documentation=tag.to_markdown(),
                    insert_text=(
                        f"{tag.name} {tag.default}"
                        if tag.default is not None
                        else tag.name
                    ),
                    category=tag.category,
                    sort_priority=0 if tag.section == current_section_path else 10,
                )
            )

        items.sort(key=lambda x: (x.sort_priority, x.label.lower()))
        return items

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _section_path_at_line(self, doc: DocumentModel, line: int) -> str:
        """返回覆盖指定行号（1-based）的 section 路径；找不到返回空字符串。"""

        def _search(sections: list[ParsedSection]) -> str | None:
            for sec in sections:
                if sec.range.start_line <= line <= sec.range.end_line:
                    # 先看子 section
                    child_path = _search(sec.children)
                    if child_path is not None:
                        return child_path
                    return sec.path
            return None

        result = _search(doc.sections)
        return result or ""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _str(val: Any) -> str:
    return str(val) if val is not None else ""


# 常见元素的默认 basis/potential 映射（GTH-PBE 系列）
_ELEMENT_BASIS_MAP: dict[str, tuple[str, str]] = {
    "H":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q1"),
    "C":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q4"),
    "N":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q5"),
    "O":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q6"),
    "F":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q7"),
    "Si": ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q4"),
    "P":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q5"),
    "S":  ("DZVP-MOLOPT-SR-GTH", "GTH-PBE-q6"),
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


def _default_basis_potential(
    element: str, p: dict[str, Any]
) -> tuple[str, str]:
    """返回元素对应的默认 basis set 和 pseudopotential 名称。"""
    if element in _ELEMENT_BASIS_MAP:
        return _ELEMENT_BASIS_MAP[element]
    # 对未知元素使用通用 GTH-PBE（不带 -q）
    return _DEFAULT_BASIS, _DEFAULT_POTENTIAL_PREFIX
