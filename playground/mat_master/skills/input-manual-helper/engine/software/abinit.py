"""
abinit.py — ABINIT 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 ABINIT 扁平 key-value 输入文件
  - render:          生成可运行的 ABINIT 输入文件（内建 Si 金刚石结构）
  - get_diagnostics: 基于 Schema 的静态校验 + ABINIT 特有物理规则
  - get_completions: 按关键字上下文返回参数建议

ABINIT 输入格式概述
--------------------
  ndtset 1
  ecut 15
  ixc 11
  acell 3*10.2632
  rprim
    0.0  0.5  0.5
    0.5  0.0  0.5
    0.5  0.5  0.0
  natom 2
  ntypat 1
  typat 1 1
  znucl 14
  xred
    0.0  0.0  0.0
    0.25 0.25 0.25
  kptopt 1
  ngkpt 4 4 4
  shiftk 0.5 0.5 0.5
  nstep 50
  toldfe 1.0d-10
  iscf 7
  pseudos "Si_r.psp8"
  # 注意：将 /opt/abinit-9.10.3/tests/Psps_for_tests/Si_r.psp8 复制到工作目录
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
# Si 晶格常数 a = 5.431 Å = 10.2632 Bohr（1 Å = 1.8897259886 Bohr）
# primitive FCC cell：acell = [10.2632, 10.2632, 10.2632] Bohr
# 归一化 rprim（无量纲）：
#   a1 = (0.0, 0.5, 0.5)
#   a2 = (0.5, 0.0, 0.5)
#   a3 = (0.5, 0.5, 0.0)
_SI_ACELL_BOHR = 10.2632  # Bohr

_SI_RPRIM = [
    (0.0, 0.5, 0.5),
    (0.5, 0.0, 0.5),
    (0.5, 0.5, 0.0),
]

_SI_FRAC_COORDS = [
    (0.000000, 0.000000, 0.000000),
    (0.250000, 0.250000, 0.250000),
]

# ---------------------------------------------------------------------------
# 赝势默认值
# ---------------------------------------------------------------------------
# ABINIT v9.10 中 ppdirpath 不是合法输入关键字，已移除
# 在 Bohrium 镜像(abinit:9.10)中赝势路径：
#   /opt/abinit-9.10.3/tests/Psps_for_tests/Si_r.psp8
# 运行前需将赝势文件复制到工作目录
_DEFAULT_SI_PSEUDO = '"Si_r.psp8"'

# ---------------------------------------------------------------------------
# 多行值关键字（后面跟 natom/ntypat 行的数值）
# ---------------------------------------------------------------------------
# 这些关键字的值紧跟在关键字后的若干行中
_MULTILINE_KEYWORDS: frozenset[str] = frozenset({
    "rprim", "xred", "xcart", "kpt", "spinat",
    "vel", "vel_orig", "acell",  # acell 通常单行，但可以多行
    "red_dfield", "shiftk",
})

# ---------------------------------------------------------------------------
# 已知的 ABINIT 参数（用于 unknown-param 检查豁免列表）
# ---------------------------------------------------------------------------
_KNOWN_PARAMS: frozenset[str] = frozenset({
    "ndtset", "ecut", "pawecutdg", "ixc", "acell", "rprim", "natom",
    "ntypat", "typat", "znucl", "xred", "xcart", "kptopt", "ngkpt",
    "shiftk", "nkpt", "kpt", "nstep", "toldfe", "tolvrs", "tolwfr",
    "iscf", "prtwf", "prtden", "prtvol", "ppdirpath", "pseudos",
    "ionmov", "ntime", "optcell", "dilatmx", "ecutsm", "npband",
    "npfft", "npkpt", "nsppol", "nspinor", "nspden", "occopt",
    "tsmear", "spinmagntarget", "charge", "tolmxf", "strfact",
    "strtarget", "nbdbuf", "nband", "istwfk", "exchn2n3d",
    "fftalg", "wfoptalg", "paral_kgb", "bandpp", "np_slk",
    "nstep_restart", "chksymbreak", "chkprim", "symmorphi",
    "getden", "getwfk", "irdden", "irdwfk", "kptbounds",
    "ndivsm", "ndivk", "wtk", "occ", "fband",
    "toldff", "strprecon", "vis", "dtion", "mdtemp",
    "restartxf", "boxcutmin", "ngfft", "ngfftdg",
    "useylm", "usepaw", "pawovlp", "pawnzlm", "pawxcdev",
    "pawstgylm", "pawntheta", "pawnphi", "pawoptmix",
    "macro_uj", "lpawu", "upawu", "jpawu",
    "optdriver", "rfelfd", "rfphon", "rfatpol", "rfdir",
    "nqpt", "qpt", "qptnrm",
    "gwcalctyp", "symsigma", "icutcoul", "vcutgeo",
    "ecutsigx", "ecuteps", "nkptgw",
    "jdtset", "udtset", "irdvdw",
    "adpimd", "adpimd_gamma", "pitransform",
})


# ---------------------------------------------------------------------------
# 解析器辅助函数
# ---------------------------------------------------------------------------

def _make_range(line: int, col_start: int, col_end: int) -> SourceRange:
    return SourceRange(
        start_line=line,
        start_col=col_start,
        end_line=line,
        end_col=col_end,
    )


def _is_keyword(token: str) -> bool:
    """判断 token 是否为 ABINIT 关键字（字母开头，后可跟字母/数字/下划线，末尾可有数字后缀）。"""
    # ABINIT 关键字可带数字后缀，如 ecut1, typat2
    return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', token))


def _parse_abinit_value(raw: str) -> Any:
    """将 ABINIT 值字符串转换为 Python 原生类型。

    处理：
    - Fortran 双精度科学计数法：1.0d-10 → 1.0e-10
    - 整数、浮点数
    - 字符串（带单/双引号）
    - 重复符号：3*10.2632 → [10.2632, 10.2632, 10.2632]（作为字符串保留）
    """
    raw = raw.strip()
    # 带引号字符串
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    # Fortran d/D 科学计数法 → Python float
    raw_norm = re.sub(r'[dD]', 'e', raw)
    # 整数
    try:
        return int(raw)
    except ValueError:
        pass
    # 浮点
    try:
        return float(raw_norm)
    except ValueError:
        pass
    # 保留原始字符串（如 3*10.2632、Si.psp8 等）
    return raw


# ---------------------------------------------------------------------------
# ABINIT 后端
# ---------------------------------------------------------------------------

class ABINITBackend(SoftwareBackend):
    """ABINIT 输入文件后端。

    完整实现 parse / render / get_diagnostics / get_completions。
    """

    software_name = "abinit"

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """解析 ABINIT 扁平 key-value 输入文件。

        ABINIT 格式特点：
        - 注释：``#`` 或 ``!`` 开头或行内出现
        - 赋值：``keyword value(s)``（关键字与值之间无 ``=``）
        - 值可跨多行（ABINIT 以"下一个关键字"作为当前关键字值的终止符）
        - 特殊多行关键字：``rprim``、``xred``、``xcart``、``kpt`` 等
        - 支持重复符号：``3*10.26`` 等（整体作为字符串值保留）

        所有参数均放在顶层的 "root" section（无嵌套）。
        """
        doc = DocumentModel(
            software="abinit",
            source=source,
            raw_text=text,
        )

        lines = text.splitlines()
        flat_params: list[ParsedParam] = []

        # 顶层 root section（ABINIT 无嵌套结构）
        root_sec = ParsedSection(
            name="root",
            range=SourceRange(1, 0, len(lines), 0),
        )

        # 逐 token 扫描策略：
        # 收集所有非注释 token 及其行号，然后按关键字边界切割
        tokens: list[tuple[str, int]] = []  # (token_str, lineno)
        for lineno, raw_line in enumerate(lines, start=1):
            # 去除行内注释（# 或 ! 开头或行内）
            line = _strip_comment(raw_line)
            for tok in line.split():
                tokens.append((tok, lineno))

        # 按"关键字"边界分组：关键字后跟若干值 token，直到下一个关键字
        # 策略：若 token 是已知关键字（或符合关键字命名规则），则开始新的 param
        i = 0
        while i < len(tokens):
            tok, lineno = tokens[i]
            # 判断是否为关键字
            if not _is_keyword(tok):
                # 无法识别的 token（可能是孤立值），跳过
                i += 1
                continue

            # 关键字名（去掉数字后缀，用于 section_path）
            kw_name = tok
            kw_start_line = lineno
            i += 1

            # 收集后续的值 token（直到下一个关键字或 EOF）
            value_tokens: list[str] = []
            value_end_line = lineno

            while i < len(tokens):
                next_tok, next_lineno = tokens[i]
                # 若下一个 token 是关键字，则停止收集
                if _is_keyword(next_tok) and _looks_like_keyword_not_value(next_tok, value_tokens):
                    break
                value_tokens.append(next_tok)
                value_end_line = next_lineno
                i += 1

            # 构造 raw_text（从文件行中提取）
            raw_text_lines = []
            for ln in range(kw_start_line, min(value_end_line + 1, len(lines) + 1)):
                raw_text_lines.append(lines[ln - 1])
            raw_text = "\n".join(raw_text_lines)

            # 解析值
            value: Any
            if not value_tokens:
                value = None
            elif len(value_tokens) == 1:
                value = _parse_abinit_value(value_tokens[0])
            else:
                # 多值：尝试解析为列表（数值数组）
                parsed_vals = [_parse_abinit_value(v) for v in value_tokens]
                # 若全部是数字，则存为列表；否则存为空格连接字符串
                if all(isinstance(v, (int, float)) for v in parsed_vals):
                    value = parsed_vals
                else:
                    value = " ".join(value_tokens)

            param = ParsedParam(
                name=kw_name,
                value=value,
                raw_text=raw_text,
                range=SourceRange(
                    start_line=kw_start_line,
                    start_col=0,
                    end_line=value_end_line,
                    end_col=len(lines[value_end_line - 1]) if value_end_line <= len(lines) else 0,
                ),
                section_path="root",
            )
            root_sec.params.append(param)
            flat_params.append(param)

        root_sec.range.end_line = len(lines)
        doc.sections = [root_sec]
        doc.params = flat_params
        return doc

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
        """根据 RenderIntent 生成可运行的 ABINIT 输入文件。

        - 默认生成 Si 金刚石 SCF 计算（primitive FCC cell，2 原子）
        - intent.task_type 决定离子运动方案
        - intent.params 可覆盖任意默认值
        - 输出文件名为 run.abi（与 bohrium-job SKILL.md 命令一致）
        """
        p = dict(intent.params) if intent.params else {}

        task = (intent.task_type or "scf").lower()

        # 离子运动参数（根据 task_type）
        needs_relax = task in ("opt", "relax", "cellopt", "vc-relax")
        needs_cellopt = task in ("cellopt", "vc-relax")

        # 覆盖参数
        if needs_relax and "ionmov" not in p:
            p["ionmov"] = 2
        if needs_relax and "ntime" not in p:
            p["ntime"] = 50
        if needs_cellopt and "optcell" not in p:
            p["optcell"] = 2
        if needs_cellopt and "dilatmx" not in p:
            p["dilatmx"] = 1.05
        if needs_cellopt and "ecutsm" not in p:
            p["ecutsm"] = 0.5

        # 读取用户自定义结构参数（若有）
        ecut = p.get("ecut", 15)
        ixc = p.get("ixc", 11)
        nstep = p.get("nstep", 50)
        iscf = p.get("iscf", 7)
        pseudos = p.get("pseudos", _DEFAULT_SI_PSEUDO)
        chksymbreak = p.get("chksymbreak", 0)

        # 收敛判据（默认只用 toldfe）
        toldfe = p.get("toldfe", None)
        tolvrs = p.get("tolvrs", None)
        tolwfr = p.get("tolwfr", None)
        # 若三者均未指定，使用默认 toldfe
        if toldfe is None and tolvrs is None and tolwfr is None:
            toldfe = "1.0d-10"

        # k 点
        ngkpt = p.get("ngkpt", "4 4 4")
        shiftk = p.get("shiftk", "0.5 0.5 0.5")
        kptopt = p.get("kptopt", 1)

        # 输出控制
        prtwf = p.get("prtwf", 1)
        prtden = p.get("prtden", 1)
        prtvol = p.get("prtvol", 0)

        lines: list[str] = []

        # ---- 文件头注释 ----
        lines.append("# ABINIT input file — Si diamond SCF (primitive FCC cell)")
        lines.append("# Generated by input-manual-helper engine (ABINITBackend)")
        lines.append("# Run with: abinit run.abi")
        lines.append("# Pseudopotential (Bohrium image abinit:9.10):")
        lines.append("#   cp /opt/abinit-9.10.3/tests/Psps_for_tests/Si_r.psp8 .")
        lines.append("# Note: ppdirpath is NOT a valid ABINIT v9 keyword; copy pseudos to workdir instead.")
        lines.append("")

        # ---- 数据集 ----
        lines.append("ndtset 1")
        lines.append("")

        # ---- 截断能 + XC ----
        lines.append(f"ecut {ecut}")
        lines.append(f"ixc {ixc}")
        lines.append("")

        # ---- 结构 ----
        lines.append("# --- Structure (Si diamond, primitive FCC cell, a=5.431 Ang) ---")
        lines.append(f"acell  3*{_SI_ACELL_BOHR}")
        lines.append("rprim")
        for row in _SI_RPRIM:
            lines.append(f"  {row[0]:.1f}  {row[1]:.1f}  {row[2]:.1f}")
        lines.append("")

        lines.append("natom  2")
        lines.append("ntypat 1")
        lines.append("typat  1 1")
        lines.append("znucl  14")
        lines.append("")

        lines.append("xred")
        for coord in _SI_FRAC_COORDS:
            lines.append(f"  {coord[0]:.6f}  {coord[1]:.6f}  {coord[2]:.6f}")
        lines.append("")

        # ---- K 点 ----
        lines.append("# --- K-points ---")
        lines.append(f"kptopt {kptopt}")
        if isinstance(ngkpt, str):
            lines.append(f"ngkpt  {ngkpt}")
        else:
            lines.append(f"ngkpt  {ngkpt}")
        if isinstance(shiftk, str):
            lines.append(f"shiftk {shiftk}")
        else:
            lines.append(f"shiftk {shiftk}")
        lines.append("")

        # ---- SCF 控制 ----
        lines.append("# --- SCF ---")
        lines.append(f"nstep  {nstep}")
        if toldfe is not None:
            lines.append(f"toldfe {toldfe}")
        if tolvrs is not None:
            lines.append(f"tolvrs {tolvrs}")
        if tolwfr is not None:
            lines.append(f"tolwfr {tolwfr}")
        lines.append(f"iscf   {iscf}")
        lines.append(f"chksymbreak {chksymbreak}")
        lines.append("")

        # ---- 离子弛豫（仅 opt/cellopt 任务）----
        if needs_relax:
            lines.append("# --- Ionic relaxation ---")
            lines.append(f"ionmov {p.get('ionmov', 2)}")
            lines.append(f"ntime  {p.get('ntime', 50)}")
            if needs_cellopt:
                lines.append(f"optcell {p.get('optcell', 2)}")
                lines.append(f"dilatmx {p.get('dilatmx', 1.05)}")
                lines.append(f"ecutsm  {p.get('ecutsm', 0.5)}")
            lines.append("")

        # ---- 输出控制 ----
        lines.append("# --- Output ---")
        lines.append(f"prtwf  {prtwf}")
        lines.append(f"prtden {prtden}")
        lines.append(f"prtvol {prtvol}")
        lines.append("")

        # ---- 赝势 ----
        lines.append("# --- Pseudopotentials ---")
        # ppdirpath 不是 ABINIT v9.10 合法关键字，不输出
        # 赝势文件需复制到工作目录后引用文件名
        pseudos_str = _ensure_quoted(pseudos)
        lines.append(f"pseudos   {pseudos_str}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """基于 Schema 做静态校验。

        检查项：
        1. ecut < 5 → warning
        2. ecut > 100 → info（非常高）
        3. nstep < 1 → error
        4. toldfe 和 tolvrs 同时设置 → warning
        5. natom 与 xred/xcart 行数不匹配 → error
        6. ntypat 与 znucl 个数不匹配 → error
        7. typat 中的类型号超过 ntypat → error
        8. 缺少 pseudos → error
        9. 缺少 ngkpt 且缺少 kpt → warning
        10. 追加 parse_errors
        """
        diags: list[Diagnostic] = []

        # ---- 收集文档中出现的参数（小写名 → param）----
        present: dict[str, ParsedParam] = {}
        for param in doc.params:
            present[param.name.lower()] = param

        # ---- 工具函数 ----
        def _get_float(name: str) -> float | None:
            p = present.get(name.lower())
            if p is None:
                return None
            try:
                raw = str(p.value).replace("d", "e").replace("D", "e")
                return float(raw)
            except (ValueError, TypeError):
                return None

        def _get_int(name: str) -> int | None:
            p = present.get(name.lower())
            if p is None:
                return None
            try:
                return int(str(p.value).split()[0])
            except (ValueError, TypeError, IndexError):
                return None

        def _get_list_len(name: str) -> int | None:
            """获取数组参数的元素个数（对于多值参数）。"""
            p = present.get(name.lower())
            if p is None:
                return None
            if isinstance(p.value, list):
                return len(p.value)
            # 字符串形式的多值（如 "14 8"）
            if isinstance(p.value, str):
                parts = p.value.split()
                return len(parts) if parts else None
            return 1

        # ---- 1. ecut 范围 ----
        ecut = _get_float("ecut")
        if ecut is not None:
            p_ecut = present["ecut"]
            if ecut < 5:
                diags.append(Diagnostic(
                    severity="warning",
                    message=f"ecut = {ecut} Ha 过小（< 5 Ha），计算结果不可靠",
                    range=p_ecut.range,
                    param="ecut",
                    suggestion="NC 赝势通常 20-40 Ha；PAW 赝势通常 8-20 Ha",
                    rule_id="ecut-too-small",
                ))
            elif ecut > 100:
                diags.append(Diagnostic(
                    severity="info",
                    message=f"ecut = {ecut} Ha 非常高（> 100 Ha），请确认是否必要",
                    range=p_ecut.range,
                    param="ecut",
                    suggestion="通常 NC 赝势 ecut = 20-40 Ha 已足够精确",
                    rule_id="ecut-very-high",
                ))

        # ---- 2. nstep < 1 ----
        nstep = _get_int("nstep")
        if nstep is not None and nstep < 1:
            diags.append(Diagnostic(
                severity="error",
                message=f"nstep = {nstep} 不合法（必须 >= 1）",
                range=present["nstep"].range,
                param="nstep",
                suggestion="设置 nstep >= 1，推荐 nstep = 50",
                rule_id="nstep-invalid",
            ))

        # ---- 3. toldfe 和 tolvrs 同时设置 ----
        has_toldfe = "toldfe" in present
        has_tolvrs = "tolvrs" in present
        has_tolwfr = "tolwfr" in present
        tol_count = sum([has_toldfe, has_tolvrs, has_tolwfr])
        if tol_count > 1:
            # 找到第一个设置的收敛判据参数位置
            first_tol = None
            for name in ("toldfe", "tolvrs", "tolwfr"):
                if name in present:
                    first_tol = present[name]
                    break
            diags.append(Diagnostic(
                severity="warning",
                message=(
                    f"同时设置了多个 SCF 收敛判据（"
                    f"{'toldfe ' if has_toldfe else ''}"
                    f"{'tolvrs ' if has_tolvrs else ''}"
                    f"{'tolwfr' if has_tolwfr else ''}）。"
                    "ABINIT 只能使用其中一个。"
                ),
                range=first_tol.range if first_tol else None,
                param="toldfe/tolvrs/tolwfr",
                suggestion="只保留一个收敛判据：SCF 推荐 toldfe；PAW 推荐 tolvrs；NSCF 推荐 tolwfr",
                rule_id="multiple-tol-criteria",
            ))

        # ---- 4. natom 与 xred/xcart 行数不匹配 ----
        natom = _get_int("natom")

        def _count_coord_rows(name: str) -> int | None:
            """统计 xred/xcart 的原子行数（每 3 个值 = 1 个原子）。"""
            p = present.get(name)
            if p is None:
                return None
            if isinstance(p.value, list):
                # 值列表长度 / 3 = 原子数
                n = len(p.value)
                return n // 3 if n % 3 == 0 else None
            if isinstance(p.value, str):
                parts = p.value.split()
                n = len(parts)
                return n // 3 if n % 3 == 0 else None
            return None

        if natom is not None:
            for coord_name in ("xred", "xcart"):
                n_rows = _count_coord_rows(coord_name)
                if n_rows is not None and n_rows != natom:
                    diags.append(Diagnostic(
                        severity="error",
                        message=(
                            f"natom = {natom} 但 {coord_name} 包含 {n_rows} 个原子坐标，"
                            "不匹配"
                        ),
                        range=present[coord_name].range,
                        param=coord_name,
                        suggestion=f"确保 {coord_name} 中有且仅有 {natom} × 3 个数值",
                        rule_id="natom-coord-mismatch",
                    ))

        # ---- 5. ntypat 与 znucl 个数不匹配 ----
        ntypat = _get_int("ntypat")
        if ntypat is not None and "znucl" in present:
            znucl_len = _get_list_len("znucl")
            if znucl_len is not None and znucl_len != ntypat:
                diags.append(Diagnostic(
                    severity="error",
                    message=(
                        f"ntypat = {ntypat} 但 znucl 包含 {znucl_len} 个元素，"
                        "不匹配"
                    ),
                    range=present["znucl"].range,
                    param="znucl",
                    suggestion=f"znucl 必须包含 {ntypat} 个原子序数",
                    rule_id="ntypat-znucl-mismatch",
                ))

        # ---- 6. typat 中类型号超过 ntypat ----
        if ntypat is not None and "typat" in present:
            typat_param = present["typat"]
            typat_vals: list[int] = []
            if isinstance(typat_param.value, list):
                for v in typat_param.value:
                    try:
                        typat_vals.append(int(v))
                    except (ValueError, TypeError):
                        pass
            elif isinstance(typat_param.value, str):
                for tok in typat_param.value.split():
                    try:
                        typat_vals.append(int(tok))
                    except ValueError:
                        pass
            elif isinstance(typat_param.value, int):
                typat_vals = [typat_param.value]

            for t in typat_vals:
                if t > ntypat or t < 1:
                    diags.append(Diagnostic(
                        severity="error",
                        message=(
                            f"typat 包含类型号 {t}，但 ntypat = {ntypat}。"
                            "类型号必须在 [1, ntypat] 范围内。"
                        ),
                        range=typat_param.range,
                        param="typat",
                        suggestion=f"typat 中的值必须在 1 到 {ntypat} 之间",
                        rule_id="typat-out-of-range",
                    ))
                    break  # 只报一次

        # ---- 7. 缺少 pseudos ----
        if "pseudos" not in present:
            diags.append(Diagnostic(
                severity="error",
                message="缺少 pseudos 参数，ABINIT 无法确定赝势文件",
                suggestion=(
                    '添加赝势参数，例如：\n'
                    'ppdirpath "/opt/abinit/share/pseudo/"\n'
                    'pseudos "Si.psp8"'
                ),
                rule_id="missing-pseudos",
            ))

        # ---- 8. 缺少 ngkpt 且缺少 kpt ----
        kptopt_val = _get_int("kptopt")
        has_ngkpt = "ngkpt" in present
        has_kpt = "nkpt" in present or "kpt" in present
        # kptopt=0 时不需要 k 点（分子计算）
        if kptopt_val != 0 and not has_ngkpt and not has_kpt:
            diags.append(Diagnostic(
                severity="warning",
                message="未设置 k 点（缺少 ngkpt 或 kpt），周期性体系计算可能不正确",
                suggestion="添加 k 点设置，例如：\nkptopt 1\nngkpt 4 4 4\nshiftk 0.5 0.5 0.5",
                rule_id="missing-kpoints",
            ))

        # ---- 9. 枚举值检查 ----
        for param in doc.params:
            tag = schema.get_tag("abinit", param.name)
            if tag is None:
                continue
            if tag.enum_values and isinstance(param.value, (int, str)):
                val_str = str(param.value)
                enum_str = [str(e) for e in tag.enum_values]
                if val_str not in enum_str:
                    diags.append(Diagnostic(
                        severity="warning",
                        message=f"{param.name} = {param.value} 不是常见枚举值",
                        range=param.range,
                        param=param.name,
                        suggestion=f"常见值: {', '.join(tag.enum_values)}",
                        rule_id="unusual-enum-value",
                    ))

        # ---- 10. 未知参数检查 ----
        for param in doc.params:
            name_lower = param.name.lower()
            # 去掉数字后缀（数据集后缀，如 ecut1 → ecut）
            name_base = re.sub(r'\d+$', '', name_lower)
            tag = schema.get_tag("abinit", param.name)
            if tag is None and name_base not in _KNOWN_PARAMS:
                diags.append(Diagnostic(
                    severity="info",
                    message=f"未知参数 '{param.name}'，不在 ABINIT Schema 中",
                    range=param.range,
                    param=param.name,
                    suggestion="检查参数名拼写，或查阅 ABINIT 官方文档",
                    rule_id="unknown-param",
                ))

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

        ABINIT 无 section 嵌套，根据光标附近已出现的参数推断当前编辑意图：
        - 优先返回与当前行相邻关键字同类别（category）的参数
        - 其次返回全部参数
        """
        # 找出光标行附近的上下文关键字（前 5 行）
        context_categories: set[str] = set()
        for param in doc.params:
            if abs(param.range.start_line - line) <= 5:
                tag = schema.get_tag("abinit", param.name)
                if tag and tag.category:
                    context_categories.add(tag.category)

        all_tags = schema.list_tags("abinit")
        items: list[CompletionItem] = []

        # 已在文档中出现的参数名（小写）
        present_names: set[str] = {p.name.lower() for p in doc.params}

        for tag in all_tags:
            # 优先级：上下文同类别 → 尚未出现的参数 → 其他
            if tag.category in context_categories:
                priority = 0
            elif tag.name.lower() not in present_names:
                priority = 5
            else:
                priority = 10

            # 生成 insert_text
            if tag.default is not None:
                insert_text = f"{tag.name} {tag.default}"
            else:
                insert_text = f"{tag.name} "

            items.append(
                CompletionItem(
                    label=tag.name,
                    detail=tag.to_completion_detail(),
                    documentation=tag.to_markdown(),
                    insert_text=insert_text,
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
        """ABINIT 无嵌套 section，始终返回 'root'。"""
        return "root"


# ---------------------------------------------------------------------------
# 模块级工具函数
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """去除行内注释（# 或 ! 开头或行内）。"""
    # 处理带引号字符串中的注释符（避免误删赝势路径中的字符）
    result = []
    in_single = False
    in_double = False
    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch in ('#', '!') and not in_single and not in_double:
            break  # 注释开始，截断
        else:
            result.append(ch)
    return "".join(result)


def _looks_like_keyword_not_value(token: str, current_values: list[str]) -> bool:
    """判断一个符合关键字命名规则的 token 是否应解释为新关键字（而非前一参数的值）。

    启发式规则：
    - 若 token 全为大写 → 值（如 Bohr, Angstrom 单位标记）
    - 若 token 是已知 ABINIT 参数名（去掉数字后缀）→ 关键字
    - 若前一个值 token 是数字或引号字符串 → 当前 token 更可能是新关键字
    - 若 current_values 为空 → 不应解释为关键字（关键字不能没有值就结束）
    """
    tok_lower = token.lower()
    # 单位标记（Bohr, Angstrom）通常是全字母且在值后面
    if token.upper() == token and token.isalpha():
        return False  # 单位标记，不是新关键字
    # 去掉数字后缀
    tok_base = re.sub(r'\d+$', '', tok_lower)
    # 是已知参数？
    if tok_base in _KNOWN_PARAMS:
        return True
    # token 是纯字母（不含数字），且不以数字开头 → 较可能是关键字
    if token.isalpha() and len(token) >= 3:
        return True
    # 若包含下划线且全为小写字母/数字/下划线 → 较可能是关键字
    if re.match(r'^[a-z][a-z0-9_]+$', token) and len(token) >= 4:
        return True
    return False


def _ensure_quoted(s: Any) -> str:
    """确保字符串值带双引号（用于 ppdirpath 和 pseudos）。"""
    s_str = str(s)
    # 已经有引号
    if (s_str.startswith('"') and s_str.endswith('"')) or \
       (s_str.startswith("'") and s_str.endswith("'")):
        return s_str
    return f'"{s_str}"'
