"""
orca.py — ORCA 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 ! keyword line / % block / * coord block
  - render:          生成可运行的 ORCA 输入文件（内建 H2O 结构）
  - get_diagnostics: 基于 Schema 的静态校验
  - get_completions: 按当前上下文返回可用参数建议

ORCA 输入格式概述
-----------------
  ! B3LYP def2-SVP tightSCF          <- keyword line（! 开头）
  %maxcore 4000                       <- 简单赋值 block
  %pal nprocs 4 end                   <- 单行 inline block
  %scf                                <- 多行 block
    MaxIter 200
  end
  * xyz 0 1                           <- 坐标块（* 开头）
  O  0.0 0.0 0.117
  H  0.0 0.757 -0.469
  H  0.0 -0.757 -0.469
  *
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
# 内建 H2O 结构（实验值，单位 Å）
# ---------------------------------------------------------------------------
_H2O_COORDS = [
    ("O", 0.000000, 0.000000, 0.117300),
    ("H", 0.000000, 0.757200, -0.469200),
    ("H", 0.000000, -0.757200, -0.469200),
]

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    "functional": "B3LYP",
    "basis": "def2-SVP",
    "scf_conv": "tightSCF",
    "maxcore": 4000,
    "nprocs": 4,
}

# keyword line 中已知的 functionals（用于 diagnostics 判断）
_FUNCTIONALS: frozenset[str] = frozenset(
    {
        "B3LYP",
        "PBE",
        "PBE0",
        "R2SCAN",
        "WB97X-D3",
        "HF",
        "DLPNO-CCSD(T)",
        "DLPNO-CCSD(T1)",
        "BLYP",
        "BP86",
        "TPSS",
        "M06",
        "M06-2X",
        "CAM-B3LYP",
        "LC-BLYP",
        "WB97X",
        "WB97X-D",
        "MP2",
        "CCSD",
        "CCSD(T)",
    }
)

# keyword line 中已知的 basis sets（用于 diagnostics 判断）
_BASIS_SETS: frozenset[str] = frozenset(
    {
        "DEF2-SVP",
        "DEF2-TZVP",
        "DEF2-TZVPP",
        "DEF2-QZVPP",
        "CC-PVDZ",
        "CC-PVTZ",
        "CC-PVQZ",
        "AUG-CC-PVDZ",
        "AUG-CC-PVTZ",
        "AUG-CC-PVQZ",
        "STO-3G",
        "3-21G",
        "6-31G",
        "6-31G*",
        "6-31G**",
        "6-311G",
        "6-311G*",
        "6-311G**",
        "MA-DEF2-SVP",
        "MA-DEF2-TZVP",
        "DEF2-SVP/C",
        "DEF2-TZVP/C",
    }
)

# keyword line 中已知的 SCF 收敛关键词
_SCF_CONV_KEYWORDS: frozenset[str] = frozenset(
    {
        "LOOSESCF",
        "NORMALSCF",
        "TIGHTSCF",
        "VERYTIGHTSCF",
    }
)

# keyword line 中已知的近似关键词
_APPROX_KEYWORDS: frozenset[str] = frozenset(
    {
        "RIJCOSX",
        "RI-JK",
        "RI",
        "NORI",
        "AUTOAUX",
        "RIJONX",
        "NORIJCOSX",
    }
)

# keyword line 中已知的任务关键词
_TASK_KEYWORDS: frozenset[str] = frozenset(
    {
        "OPT",
        "FREQ",
        "OPTFREQ",
        "NEB",
        "IRC",
        "SP",
        "ENERGY",
        "ENGRAD",
        "NUMFREQ",
        "MD",
        "NEB-TS",
        "GOAT",
    }
)

# 所有在 keyword line 中合法的关键词（大写）
_ALL_KW_LINE_KEYWORDS: frozenset[str] = (
    _FUNCTIONALS | _BASIS_SETS | _SCF_CONV_KEYWORDS | _APPROX_KEYWORDS | _TASK_KEYWORDS
)


# ---------------------------------------------------------------------------
# 解析器辅助
# ---------------------------------------------------------------------------


def _parse_value(raw: str) -> Any:
    """尝试将字符串转换为 Python 原生类型。"""
    raw = raw.strip()
    if raw.upper() in ("TRUE", ".TRUE.", "T", "YES"):
        return True
    if raw.upper() in ("FALSE", ".FALSE.", "F", "NO"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _make_range(line: int, col_start: int, col_end: int) -> SourceRange:
    return SourceRange(
        start_line=line,
        start_col=col_start,
        end_line=line,
        end_col=col_end,
    )


# ---------------------------------------------------------------------------
# ORCA 后端
# ---------------------------------------------------------------------------


class ORCABackend(SoftwareBackend):
    """ORCA 输入文件后端。

    完整实现 parse / render / get_diagnostics / get_completions。
    """

    software_name = "orca"

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """解析 ORCA 输入文件。

        识别三种结构：
        1. ``! keyword...`` → 特殊 section "keyword_line"
        2. ``%block_name ...`` → ParsedSection，内部 key value → ParsedParam
        3. ``* xyz charge mult`` → ParsedSection "coord_block"

        ⚠️ 注意 ``%pal nprocs 32 end`` 这种单行 inline 形式：
           ``end`` 在同一行，不应把后续行当作 block body。
        """
        doc = DocumentModel(
            software="orca",
            source=source,
            raw_text=text,
        )

        lines = text.splitlines()
        flat_params: list[ParsedParam] = []
        top_sections: list[ParsedSection] = []

        # 解析状态
        in_block: ParsedSection | None = None  # 当前 % block（多行）
        in_coord: ParsedSection | None = None  # 当前 * coord block

        # 正则
        _re_kw_line = re.compile(r"^\s*!(.*)")
        _re_block_open = re.compile(r"^\s*%(\w+)\s*(.*)", re.IGNORECASE)
        _re_coord_open = re.compile(r"^\s*\*\s*(\w+)\s+(.*)", re.IGNORECASE)
        _re_coord_close = re.compile(r"^\s*\*\s*$")
        _re_block_end = re.compile(r"^\s*end\s*$", re.IGNORECASE)
        _re_kv = re.compile(r"^\s*(\w+)\s+(.*\S)\s*$")

        for lineno, raw_line in enumerate(lines, start=1):
            # 去掉行末注释（# 开头，但 ! 在 ORCA 中是关键字行标志，不是注释）
            stripped = raw_line
            # ORCA 行内注释用 #
            hash_idx = stripped.find("#")
            if hash_idx >= 0:
                stripped = stripped[:hash_idx]
            stripped = stripped.strip()

            if not stripped:
                continue

            # ----------------------------------------------------------------
            # 坐标块内部（* xyz ... 到 * 之间）
            # ----------------------------------------------------------------
            if in_coord is not None:
                if _re_coord_close.match(stripped) or stripped == "*":
                    # 坐标块结束
                    in_coord.range.end_line = lineno
                    in_coord.range.end_col = len(raw_line)
                    top_sections.append(in_coord)
                    in_coord = None
                else:
                    # 原子坐标行，存为 ParsedParam，name=元素符号，value=坐标字符串
                    parts = stripped.split()
                    atom_name = parts[0] if parts else "?"
                    param = ParsedParam(
                        name=atom_name,
                        value=stripped,
                        raw_text=raw_line,
                        range=_make_range(lineno, 0, len(raw_line)),
                        section_path="coord_block",
                    )
                    if in_coord is not None:
                        in_coord.params.append(param)
                    flat_params.append(param)
                continue

            # ----------------------------------------------------------------
            # % block 内部（多行模式）
            # ----------------------------------------------------------------
            if in_block is not None:
                if _re_block_end.match(stripped):
                    in_block.range.end_line = lineno
                    in_block.range.end_col = len(raw_line)
                    top_sections.append(in_block)
                    in_block = None
                else:
                    m_kv = _re_kv.match(stripped)
                    if m_kv:
                        kw = m_kv.group(1)
                        val_raw = m_kv.group(2).strip()
                        val = _parse_value(val_raw)
                        param = ParsedParam(
                            name=kw,
                            value=val,
                            raw_text=raw_line,
                            range=_make_range(lineno, 0, len(raw_line)),
                            section_path=in_block.name,
                        )
                        in_block.params.append(param)
                        flat_params.append(param)
                continue

            # ----------------------------------------------------------------
            # ! keyword line
            # ----------------------------------------------------------------
            m_kw_line = _re_kw_line.match(stripped)
            if m_kw_line:
                kw_content = m_kw_line.group(1).strip()
                kw_sec = ParsedSection(
                    name="keyword_line",
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                )
                # 将每个空格分隔的词存为 ParsedParam
                for token in kw_content.split():
                    param = ParsedParam(
                        name=token,
                        value=token,
                        raw_text=token,
                        range=_make_range(lineno, 0, len(raw_line)),
                        section_path="keyword_line",
                    )
                    kw_sec.params.append(param)
                    flat_params.append(param)
                top_sections.append(kw_sec)
                continue

            # ----------------------------------------------------------------
            # % block 开始
            # ----------------------------------------------------------------
            m_block = _re_block_open.match(stripped)
            if m_block:
                block_name = "%" + m_block.group(1).lower()
                rest = m_block.group(2).strip()

                new_sec = ParsedSection(
                    name=block_name,
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                )

                # 检查是否是简单赋值（%maxcore 4000）：rest 是单个值，无 end
                rest.upper()
                rest_tokens = rest.split()

                if not rest:
                    # 纯多行 block（%scf\n...\nend）
                    in_block = new_sec
                    continue

                # 检查是否 inline 结束（rest 末尾含 end）
                # 例如：%pal nprocs 4 end
                if rest_tokens and rest_tokens[-1].upper() == "END":
                    # inline block，去掉末尾的 end 处理内容
                    inner = " ".join(rest_tokens[:-1]).strip()
                    # 解析 inner 中的 key-value 对
                    inner_tokens = inner.split()
                    i = 0
                    while i + 1 < len(inner_tokens):
                        kw = inner_tokens[i]
                        val = _parse_value(inner_tokens[i + 1])
                        param = ParsedParam(
                            name=kw,
                            value=val,
                            raw_text=raw_line,
                            range=_make_range(lineno, 0, len(raw_line)),
                            section_path=block_name,
                        )
                        new_sec.params.append(param)
                        flat_params.append(param)
                        i += 2
                    new_sec.range.end_line = lineno
                    new_sec.range.end_col = len(raw_line)
                    top_sections.append(new_sec)
                else:
                    # 简单赋值：%maxcore 4000（rest 不含 end，且是单值）
                    if len(rest_tokens) == 1:
                        param = ParsedParam(
                            name=block_name[1:],  # 去掉 %
                            value=_parse_value(rest_tokens[0]),
                            raw_text=raw_line,
                            range=_make_range(lineno, 0, len(raw_line)),
                            section_path=block_name,
                        )
                        new_sec.params.append(param)
                        flat_params.append(param)
                        new_sec.range.end_line = lineno
                        new_sec.range.end_col = len(raw_line)
                        top_sections.append(new_sec)
                    else:
                        # 多个 token，无 end → 当作多行 block 的首行有 inline 参数
                        # 先解析 rest 作为 key-value
                        inner_tokens = rest_tokens
                        i = 0
                        while i + 1 < len(inner_tokens):
                            kw = inner_tokens[i]
                            val = _parse_value(inner_tokens[i + 1])
                            param = ParsedParam(
                                name=kw,
                                value=val,
                                raw_text=raw_line,
                                range=_make_range(lineno, 0, len(raw_line)),
                                section_path=block_name,
                            )
                            new_sec.params.append(param)
                            flat_params.append(param)
                            i += 2
                        in_block = new_sec
                continue

            # ----------------------------------------------------------------
            # * coord block 开始
            # ----------------------------------------------------------------
            m_coord = _re_coord_open.match(stripped)
            if m_coord:
                _coord_type = m_coord.group(  # noqa: F841
                    1
                ).lower()  # "xyz", "int", "gzmt"
                rest = m_coord.group(2).strip()
                # 解析 charge 和 mult
                parts = rest.split()
                charge = 0
                mult = 1
                if len(parts) >= 2:
                    try:
                        charge = int(parts[0])
                        mult = int(parts[1])
                    except ValueError:
                        doc.parse_errors.append(
                            Diagnostic(
                                severity="error",
                                message=f"坐标块头行 charge/mult 格式错误：'{rest}'",
                                range=_make_range(lineno, 0, len(raw_line)),
                                rule_id="coord-header-format-error",
                            )
                        )
                elif len(parts) == 1:
                    doc.parse_errors.append(
                        Diagnostic(
                            severity="error",
                            message=f"坐标块头行缺少 multiplicity：'{stripped}'",
                            range=_make_range(lineno, 0, len(raw_line)),
                            rule_id="coord-header-missing-mult",
                        )
                    )
                else:
                    doc.parse_errors.append(
                        Diagnostic(
                            severity="error",
                            message=f"坐标块头行缺少 charge 和 multiplicity：'{stripped}'",
                            range=_make_range(lineno, 0, len(raw_line)),
                            rule_id="coord-header-missing-charge-mult",
                        )
                    )

                new_coord = ParsedSection(
                    name="coord_block",
                    range=SourceRange(lineno, 0, lineno, len(raw_line)),
                )
                # 存储 charge 和 mult 作为 params
                for pname, pval in [("charge", charge), ("multiplicity", mult)]:
                    param = ParsedParam(
                        name=pname,
                        value=pval,
                        raw_text=raw_line,
                        range=_make_range(lineno, 0, len(raw_line)),
                        section_path="coord_block",
                    )
                    new_coord.params.append(param)
                    flat_params.append(param)

                in_coord = new_coord
                continue

            # 无法识别的行 → 忽略

        # 处理未关闭的 block
        if in_block is not None:
            doc.parse_errors.append(
                Diagnostic(
                    severity="warning",
                    message=f"Block {in_block.name} 未正常关闭（缺少 end）",
                    range=_make_range(in_block.range.start_line, 0, 0),
                    rule_id="unclosed-block",
                )
            )
            top_sections.append(in_block)

        if in_coord is not None:
            doc.parse_errors.append(
                Diagnostic(
                    severity="warning",
                    message="坐标块未正常关闭（缺少结尾 *）",
                    range=_make_range(in_coord.range.start_line, 0, 0),
                    rule_id="unclosed-coord-block",
                )
            )
            top_sections.append(in_coord)

        doc.sections = top_sections
        doc.params = flat_params
        return doc

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
        """根据 RenderIntent 生成可运行的 ORCA 输入文件。

        - 默认生成 H2O 单点能（B3LYP/def2-SVP）
        - intent.params 可覆盖 functional、basis、maxcore、nprocs 等
        - intent.structure_file 提供时尝试用 pymatgen/ASE 加载分子结构
        - intent.task_type 决定任务类型（scf/opt/freq/tddft）
        """
        p = dict(_DEFAULTS)
        for k, v in intent.params.items():
            p[k.lower()] = v

        functional = str(p.get("functional", "B3LYP"))
        basis = str(p.get("basis", "def2-SVP"))
        scf_conv = str(p.get("scf_conv", "tightSCF"))
        maxcore = int(p.get("maxcore", 4000))
        nprocs = int(p.get("nprocs", 4))
        charge = intent.charge
        mult = intent.spin_multiplicity

        task = (intent.task_type or "scf").lower()

        # ---- 构建 keyword line ----
        kw_tokens: list[str] = [functional, basis, scf_conv]
        if task == "opt":
            kw_tokens.append("Opt")
        elif task == "freq":
            kw_tokens.append("Freq")
        elif task == "optfreq":
            kw_tokens.append("OptFreq")

        lines: list[str] = []

        # ---- 文件头注释 ----
        lines.append("# ORCA input file — H2O single point (B3LYP/def2-SVP)")
        lines.append("# Generated by input-manual-helper engine (ORCABackend)")
        lines.append("# Bohrium command: /opt/orca/orca input.inp")
        lines.append("")

        lines.append("! " + " ".join(kw_tokens))

        # ---- %maxcore ----
        lines.append(f"%maxcore {maxcore}")

        # ---- %pal ----
        lines.append(f"%pal nprocs {nprocs} end")

        # ---- %tddft block（仅 tddft 任务）----
        if task == "tddft":
            nroots = int(p.get("nroots", 5))
            lines += [
                "%tddft",
                f"  NRoots {nroots}",
                "end",
            ]

        # ---- %scf block（如果有自定义 SCF 参数）----
        scf_maxiter = p.get("scf_maxiter")
        if scf_maxiter is not None:
            lines += [
                "%scf",
                f"  MaxIter {int(scf_maxiter)}",
                "end",
            ]

        lines.append("")  # 空行分隔

        # ---- 坐标块 ----
        coord_lines = self._build_coord_lines(intent)
        lines.append(f"* xyz {charge} {mult}")
        lines.extend(coord_lines)
        lines.append("*")
        lines.append("")

        return "\n".join(lines)

    def _build_coord_lines(self, intent: RenderIntent) -> list[str]:
        """构建坐标行列表。

        若 intent.structure_file 提供，尝试用 pymatgen 或 ASE 读取；
        失败则回退到内建 H2O 结构。
        """
        if intent.structure_file:
            result = self._try_load_molecule_pymatgen(intent.structure_file)
            if result is not None:
                return result
            result = self._try_load_molecule_ase(intent.structure_file)
            if result is not None:
                return result

        return self._builtin_h2o_coords()

    def _try_load_molecule_pymatgen(self, path: str) -> list[str] | None:
        """尝试用 pymatgen 加载分子结构文件。"""
        try:
            from pymatgen.core import Molecule  # type: ignore

            mol = Molecule.from_file(path)
            result = []
            for site in mol:
                x, y, z = site.coords
                result.append(f"{site.specie.symbol}   {x:.6f}   {y:.6f}   {z:.6f}")
            return result
        except Exception:  # noqa: BLE001
            return None

    def _try_load_molecule_ase(self, path: str) -> list[str] | None:
        """尝试用 ASE 加载分子结构文件。"""
        try:
            from ase.io import read  # type: ignore

            atoms = read(path)
            result = []
            for symbol, (x, y, z) in zip(
                atoms.get_chemical_symbols(), atoms.get_positions()
            ):
                result.append(f"{symbol}   {x:.6f}   {y:.6f}   {z:.6f}")
            return result
        except Exception:  # noqa: BLE001
            return None

    def _builtin_h2o_coords(self) -> list[str]:
        """返回内建 H2O 结构的坐标行（单位 Å）。"""
        return [f"{sym}   {x:.6f}   {y:.6f}   {z:.6f}" for sym, x, y, z in _H2O_COORDS]

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """基于 Schema 做静态校验。

        检查项：
        1. %maxcore 范围（<100 → warning，>64000 → warning）
        2. %pal nprocs < 1 → error
        3. keyword line 无 functional → warning
        4. keyword line 无 basis set → warning
        5. 坐标块缺失 → error
        6. DLPNO-CCSD(T) 无 tightSCF → info
        7. parse_errors 追加
        """
        diags: list[Diagnostic] = []

        # 收集 keyword line 中的所有词（大写）
        kw_tokens_upper: set[str] = set()
        for sec in doc.sections:
            if sec.name == "keyword_line":
                for param in sec.params:
                    kw_tokens_upper.add(param.name.upper())

        # 1. %maxcore 范围检查
        maxcore_param = self._find_block_param(doc, "%maxcore", "maxcore")
        if maxcore_param is not None:
            try:
                val = int(float(str(maxcore_param.value)))
                if val < 100:
                    diags.append(
                        Diagnostic(
                            severity="warning",
                            message=f"%maxcore {val} 过小（< 100 MB），ORCA 可能无法正常运行",
                            range=maxcore_param.range,
                            param="maxcore",
                            suggestion="建议 %maxcore >= 1000（MB）",
                            rule_id="maxcore-too-small",
                        )
                    )
                elif val > 64000:
                    diags.append(
                        Diagnostic(
                            severity="warning",
                            message=f"%maxcore {val} 超出典型限制（> 64000 MB）",
                            range=maxcore_param.range,
                            param="maxcore",
                            suggestion="请确认节点内存充足",
                            rule_id="maxcore-too-large",
                        )
                    )
            except (ValueError, TypeError):
                pass

        # 2. %pal nprocs < 1 检查
        nprocs_param = self._find_block_param(doc, "%pal", "nprocs")
        if nprocs_param is not None:
            try:
                val = int(float(str(nprocs_param.value)))
                if val < 1:
                    diags.append(
                        Diagnostic(
                            severity="error",
                            message=f"%pal nprocs {val} 无效（必须 >= 1）",
                            range=nprocs_param.range,
                            param="nprocs",
                            suggestion="设置 nprocs >= 1",
                            rule_id="nprocs-invalid",
                        )
                    )
            except (ValueError, TypeError):
                pass

        # 3. keyword line 中无 functional → warning
        has_functional = bool(kw_tokens_upper & _FUNCTIONALS)
        if not has_functional:
            kw_sec = self._find_keyword_line_section(doc)
            diags.append(
                Diagnostic(
                    severity="warning",
                    message="keyword line（! 行）中未指定泛函（functional）",
                    range=kw_sec.range if kw_sec else None,
                    param="functional",
                    suggestion="在 ! 行添加泛函，如 B3LYP、PBE0",
                    rule_id="missing-functional",
                )
            )

        # 4. keyword line 中无 basis set → warning
        has_basis = bool(kw_tokens_upper & _BASIS_SETS)
        if not has_basis:
            kw_sec = self._find_keyword_line_section(doc)
            diags.append(
                Diagnostic(
                    severity="warning",
                    message="keyword line（! 行）中未指定基组（basis set）",
                    range=kw_sec.range if kw_sec else None,
                    param="basis",
                    suggestion="在 ! 行添加基组，如 def2-SVP、def2-TZVP",
                    rule_id="missing-basis",
                )
            )

        # 5. 坐标块缺失 → error
        coord_sec = doc.get_section("coord_block")
        if coord_sec is None:
            diags.append(
                Diagnostic(
                    severity="error",
                    message="缺少坐标块（* xyz charge mult ... *）",
                    suggestion="添加分子坐标块，例如：\n* xyz 0 1\nO 0.0 0.0 0.117\n...\n*",
                    rule_id="missing-coord-block",
                )
            )

        # 6. DLPNO-CCSD(T) 无 tightSCF → info 建议
        has_dlpno = bool(kw_tokens_upper & {"DLPNO-CCSD(T)", "DLPNO-CCSD(T1)"})
        has_tight = bool(kw_tokens_upper & {"TIGHTSCF", "VERYTIGHTSCF"})
        if has_dlpno and not has_tight:
            kw_sec = self._find_keyword_line_section(doc)
            diags.append(
                Diagnostic(
                    severity="info",
                    message="使用 DLPNO-CCSD(T) 时建议同时指定 tightSCF 以确保精度",
                    range=kw_sec.range if kw_sec else None,
                    param="tightSCF",
                    suggestion="在 ! 行添加 tightSCF",
                    rule_id="dlpno-needs-tightscf",
                )
            )

        # 7. 追加解析阶段错误
        diags.extend(doc.parse_errors)
        return diags

    def _find_block_param(
        self,
        doc: DocumentModel,
        block_name: str,
        param_name: str,
    ) -> ParsedParam | None:
        """在指定 % block section 中查找参数。"""
        for sec in doc.sections:
            if sec.name == block_name:
                for param in sec.params:
                    if param.name.lower() == param_name.lower():
                        return param
        return None

    def _find_keyword_line_section(self, doc: DocumentModel) -> ParsedSection | None:
        """返回第一个 keyword_line section。"""
        for sec in doc.sections:
            if sec.name == "keyword_line":
                return sec
        return None

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

        - 在 ! 行 → 建议 functionals、basis sets、approximations、task keywords
        - 在 % block 内 → 建议该 block 的参数
        - 其他位置 → 返回所有可用 schema 参数
        """
        context = self._context_at_line(doc, line)
        all_tags = schema.list_tags("orca")
        items: list[CompletionItem] = []

        if context == "keyword_line":
            # 补全 keyword line 中可用的关键词
            for tag in all_tags:
                if tag.section == "keyword_line":
                    items.append(
                        CompletionItem(
                            label=tag.name,
                            detail=tag.to_completion_detail(),
                            documentation=tag.to_markdown(),
                            insert_text=tag.name,
                            category=tag.category,
                            sort_priority=0,
                        )
                    )
        elif context.startswith("%"):
            # 在 % block 内，补全该 block 的参数
            block_name = context  # e.g. "%scf"
            for tag in all_tags:
                if tag.section and tag.section.lower() == block_name.lower():
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
                            sort_priority=0,
                        )
                    )
            # 若该 block 无专属参数，退回到全部
            if not items:
                for tag in all_tags:
                    items.append(
                        CompletionItem(
                            label=tag.name,
                            detail=tag.to_completion_detail(),
                            documentation=tag.to_markdown(),
                            insert_text=tag.name,
                            category=tag.category,
                            sort_priority=10,
                        )
                    )
        else:
            # 默认：返回所有 schema 参数
            for tag in all_tags:
                items.append(
                    CompletionItem(
                        label=tag.name,
                        detail=tag.to_completion_detail(),
                        documentation=tag.to_markdown(),
                        insert_text=tag.name,
                        category=tag.category,
                        sort_priority=10,
                    )
                )

        items.sort(key=lambda x: (x.sort_priority, x.label.lower()))
        return items

    def _context_at_line(self, doc: DocumentModel, line: int) -> str:
        """返回指定行所在的 section 名称（keyword_line / %xxx / coord_block / ''）。"""
        for sec in doc.sections:
            if sec.range.start_line <= line <= sec.range.end_line:
                return sec.name
        return ""
