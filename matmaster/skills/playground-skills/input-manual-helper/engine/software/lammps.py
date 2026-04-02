"""
lammps.py — LAMMPS 软件后端完整实现。

实现 SoftwareBackend 的四个核心方法：
  - parse:           解析 LAMMPS 命令式脚本输入文件
  - render:          生成可运行的 LAMMPS 输入文件（LJ 势 FCC 能量最小化）
  - get_diagnostics: 基于 Schema 的静态校验 + LAMMPS 特有物理规则
  - get_completions: 按命令上下文返回参数建议

LAMMPS 输入格式概述
--------------------
  units           metal
  dimension       3
  boundary        p p p
  atom_style      atomic

  lattice         fcc 3.615
  region          box block 0 4 0 4 0 4
  create_box      1 box
  create_atoms    1 box
  mass            1 63.546

  pair_style      lj/cut 2.5
  pair_coeff      1 1 1.0 1.0 2.5

  neighbor        0.3 bin
  neigh_modify    delay 5

  thermo          100
  thermo_style    custom step pe ke etotal press vol

  min_style       cg
  minimize        1.0e-6 1.0e-8 1000 10000
"""

from __future__ import annotations

from typing import Any

from engine.completion import CompletionItem
from engine.diagnostics import Diagnostic
from engine.document import DocumentModel, ParsedParam, ParsedSection, SourceRange
from engine.renderer import RenderIntent
from engine.schema import SchemaRegistry
from engine.software.base import SoftwareBackend

# ---------------------------------------------------------------------------
# 常量：已知 LAMMPS 命令集合（用于 unknown_command 检查豁免）
# ---------------------------------------------------------------------------
_KNOWN_COMMANDS: frozenset[str] = frozenset(
    {
        "units",
        "dimension",
        "boundary",
        "atom_style",
        "atom_modify",
        "lattice",
        "region",
        "create_box",
        "create_atoms",
        "read_data",
        "read_restart",
        "replicate",
        "pair_style",
        "pair_coeff",
        "pair_modify",
        "pair_write",
        "bond_style",
        "bond_coeff",
        "angle_style",
        "angle_coeff",
        "dihedral_style",
        "dihedral_coeff",
        "improper_style",
        "improper_coeff",
        "kspace_style",
        "kspace_modify",
        "mass",
        "velocity",
        "set",
        "group",
        "group2ndx",
        "ndx2group",
        "neighbor",
        "neigh_modify",
        "timestep",
        "thermo",
        "thermo_style",
        "thermo_modify",
        "fix",
        "fix_modify",
        "unfix",
        "compute",
        "compute_modify",
        "uncompute",
        "dump",
        "dump_modify",
        "undump",
        "run",
        "run_style",
        "minimize",
        "min_style",
        "min_modify",
        "reset_timestep",
        "reset_atoms",
        "variable",
        "next",
        "print",
        "log",
        "echo",
        "info",
        "write_data",
        "write_restart",
        "restart",
        "change_box",
        "displace_atoms",
        "delete_atoms",
        "shell",
        "include",
        "jump",
        "label",
        "if",
        "then",
        "else",
        "quit",
        "clear",
        "processors",
        "partition",
        "suffix",
        "package",
        "accelerate",
        "kim_init",
        "kim_interactions",
        "kim_query",
        "kim_param",
    }
)

# pair_style 名称（用于补全）
_PAIR_STYLE_NAMES: list[str] = [
    "lj/cut",
    "lj/cut/coul/cut",
    "lj/cut/coul/long",
    "eam",
    "eam/alloy",
    "eam/fs",
    "morse",
    "tersoff",
    "sw",
    "buck",
    "buck/coul/cut",
    "buck/coul/long",
    "coul/cut",
    "coul/long",
    "hybrid",
    "hybrid/overlay",
    "zero",
    "none",
]

# units 选项
_UNITS_OPTIONS: list[str] = [
    "lj",
    "real",
    "metal",
    "si",
    "cgs",
    "electron",
    "micro",
    "nano",
]

# timestep 合理范围（基于 units）：(min, max) in native time units
_TIMESTEP_RANGES: dict[str, tuple[float, float]] = {
    "real": (0.0001, 10.0),  # fs
    "metal": (0.00001, 0.01),  # ps
    "lj": (0.0001, 0.01),  # tau
    "si": (1e-16, 1e-12),  # s
    "cgs": (1e-16, 1e-12),  # s
    "electron": (0.0001, 1.0),  # fs
    "micro": (1e-6, 1.0),  # us
    "nano": (1e-6, 1.0),  # ns
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_range(line: int, col_start: int, col_end: int) -> SourceRange:
    """构造单行 SourceRange。"""
    return SourceRange(
        start_line=line,
        start_col=col_start,
        end_line=line,
        end_col=col_end,
    )


def _strip_comment(line: str) -> str:
    """去除行内注释（# 开头或行内出现）。保留引号内的 #。"""
    result: list[str] = []
    in_single = False
    in_double = False
    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == '#' and not in_single and not in_double:
            break
        else:
            result.append(ch)
    return "".join(result)


def _join_continuation_lines(lines: list[str]) -> list[tuple[int, str]]:
    """处理续行符 &（行尾），合并多行为逻辑行。

    返回 [(original_lineno, logical_line), ...]，lineno 为 1-based，
    指向逻辑行的第一个物理行。
    """
    result: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        logical_parts: list[str] = []
        start_lineno = i + 1  # 1-based
        while i < len(lines):
            raw = lines[i]
            stripped = _strip_comment(raw).rstrip()
            if stripped.endswith("&"):
                logical_parts.append(stripped[:-1])
                i += 1
            else:
                logical_parts.append(stripped)
                i += 1
                break
        logical_line = " ".join(logical_parts).strip()
        if logical_line:
            result.append((start_lineno, logical_line))
    return result


# ---------------------------------------------------------------------------
# SchemaRegistry 软件绑定代理（让 list_tags() 无需传 software 参数）
# ---------------------------------------------------------------------------


class _BoundRegistry:
    """将 SchemaRegistry 绑定到特定软件的代理对象。

    使 ``backend.registry.list_tags()`` 可不传 software 参数，
    与验证脚本的调用方式兼容。
    """

    def __init__(self, registry: SchemaRegistry, software: str) -> None:
        self._registry = registry
        self._software = software

    def list_tags(self, category: str | None = None):
        return self._registry.list_tags(self._software, category)

    def get_tag(self, param_name: str):
        return self._registry.get_tag(self._software, param_name)

    def search_tags(self, query: str):
        return self._registry.search_tags(self._software, query)

    def get_all_categories(self):
        return self._registry.get_all_categories(self._software)

    # 透传底层 registry 的其他属性/方法
    def __getattr__(self, name: str):
        return getattr(self._registry, name)


# ---------------------------------------------------------------------------
# LAMMPS 后端
# ---------------------------------------------------------------------------


class LAMMPSBackend(SoftwareBackend):
    """LAMMPS 输入脚本后端。

    完整实现 parse / render / get_diagnostics / get_completions。
    内部维护 SchemaRegistry 实例，无需外部传入。
    ``registry`` 属性为绑定到 lammps 软件的代理，可直接调用
    ``registry.list_tags()``（无需传 software 参数）。
    """

    software_name = "lammps"

    def __init__(self) -> None:
        _raw = SchemaRegistry()
        _raw.load_software("lammps")
        self.registry = _BoundRegistry(_raw, "lammps")
        self._schema = _raw  # 内部使用原始 registry

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """解析 LAMMPS 命令式脚本输入文件。

        LAMMPS 格式特点：
        - 注释以 ``#`` 开头（行内或行首）
        - 每行一条命令：``command arg1 arg2 ...``
        - 续行符 ``&`` 在行尾（连接下一行）
        - 无 section 嵌套结构，所有命令放在顶层 "commands" section

        解析策略：
        1. 处理续行符，合并为逻辑行
        2. 每条逻辑行的第一个 token 为命令名（param.name）
        3. 其余 tokens 合并为值（param.value）
        """
        doc = DocumentModel(
            software="lammps",
            source=source,
            raw_text=text,
        )

        raw_lines = text.splitlines()
        total_lines = len(raw_lines)

        # 顶层 "commands" section（LAMMPS 无嵌套）
        root_sec = ParsedSection(
            name="commands",
            range=SourceRange(1, 0, max(total_lines, 1), 0),
        )
        doc.sections.append(root_sec)

        # 处理续行符，获得逻辑行列表
        logical_lines = _join_continuation_lines(raw_lines)

        for lineno, logical_line in logical_lines:
            tokens = logical_line.split()
            if not tokens:
                continue

            command = tokens[0].lower()
            value_tokens = tokens[1:]
            value_str = " ".join(value_tokens) if value_tokens else ""

            # 计算列范围（基于命令在原始行中的位置）
            raw_line = raw_lines[lineno - 1] if lineno - 1 < len(raw_lines) else ""
            col_start = len(raw_line) - len(raw_line.lstrip())
            col_end = col_start + len(command)

            param = ParsedParam(
                name=command,
                value=value_str,
                raw_text=logical_line,
                range=_make_range(lineno, col_start, col_end),
                section_path="commands",
            )
            root_sec.params.append(param)
            doc.params.append(param)

        return doc

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------

    def render(self, intent: RenderIntent) -> str:
        """根据 RenderIntent 生成 LAMMPS 输入文件文本。

        默认测试用例：LJ 势 FCC 晶体能量最小化（不依赖外部文件）。
        task_type 映射：
          - 'minimize' / 'min' → 能量最小化（默认）
          - 'md' / 'nve'       → NVE MD
          - 'nvt'              → NVT MD（Nosé-Hoover 恒温器）
          - 'npt'              → NPT MD（Nosé-Hoover 恒温/压）
        """
        p: dict[str, Any] = {}
        if intent.params:
            p = dict(intent.params)

        task = (intent.task_type or "minimize").lower()
        is_md = task in ("md", "nve", "nvt", "npt")
        is_nvt = task == "nvt"
        is_npt = task == "npt"

        # ---- 默认参数 ----
        units_val = p.get("units", "lj")
        dimension_val = p.get("dimension", 3)
        boundary_val = p.get("boundary", "p p p")
        atom_style_val = p.get("atom_style", "atomic")

        # LJ 方案：lattice fcc 0.8442（约等于液氩近熔点密度）
        lattice_style = p.get("lattice_style", "fcc")
        lattice_scale = p.get("lattice_scale", "0.8442")
        box_lo = p.get("box_lo", 0)
        box_hi = p.get("box_hi", 4)
        mass_val = p.get("mass", "1 1.0")

        pair_style_val = p.get("pair_style", "lj/cut 2.5")
        pair_coeff_val = p.get("pair_coeff", "1 1 1.0 1.0 2.5")

        neighbor_val = p.get("neighbor", "0.3 bin")
        neigh_modify_val = p.get("neigh_modify", "delay 5")

        thermo_val = p.get("thermo", 100)
        thermo_style_val = p.get("thermo_style", "custom step pe ke etotal press vol")

        lines: list[str] = []

        # ---- 文件头注释 ----
        lines.append("# LAMMPS input file — LJ FCC energy minimization / MD")
        lines.append("# Generated by input-manual-helper engine (LAMMPSBackend)")
        lines.append("# Bohrium command: lmp -in input.lammps")
        lines.append("")

        # ---- 初始化 ----
        lines.append("# LAMMPS input: LJ FCC energy minimization / MD")
        lines.append(f"units           {units_val}")
        lines.append(f"dimension       {dimension_val}")
        lines.append(f"boundary        {boundary_val}")
        lines.append(f"atom_style      {atom_style_val}")
        lines.append("")

        # ---- 晶格和模拟盒子 ----
        lines.append(f"lattice         {lattice_style} {lattice_scale}")
        lines.append(
            f"region          box block {box_lo} {box_hi} "
            f"{box_lo} {box_hi} {box_lo} {box_hi}"
        )
        lines.append("create_box      1 box")
        lines.append("create_atoms    1 box")
        lines.append(f"mass            {mass_val}")
        lines.append("")

        # ---- 势函数 ----
        lines.append(f"pair_style      {pair_style_val}")
        lines.append(f"pair_coeff      {pair_coeff_val}")
        lines.append("")

        # ---- 邻居列表 ----
        lines.append(f"neighbor        {neighbor_val}")
        lines.append(f"neigh_modify    {neigh_modify_val}")
        lines.append("")

        # ---- 热力学输出 ----
        lines.append(f"thermo          {thermo_val}")
        lines.append(f"thermo_style    {thermo_style_val}")
        lines.append("")

        if is_md:
            # ---- MD 任务 ----
            temp = p.get("temp", 1.0)
            run_steps = p.get("run", 10000)

            # 初始化速度
            seed = p.get("velocity_seed", 12345)
            lines.append(f"velocity        all create {temp} {seed} dist gaussian")
            lines.append("")

            if is_npt:
                press = p.get("press", 0.0)
                tdamp = p.get("tdamp", 0.1)
                pdamp = p.get("pdamp", 1.0)
                lines.append(
                    f"fix             1 all npt temp {temp} {temp} {tdamp} "
                    f"iso {press} {press} {pdamp}"
                )
            elif is_nvt:
                tdamp = p.get("tdamp", 0.1)
                lines.append(f"fix             1 all nvt temp {temp} {temp} {tdamp}")
            else:
                # NVE
                lines.append("fix             1 all nve")

            lines.append("")
            lines.append(f"run             {run_steps}")

        else:
            # ---- 能量最小化任务（默认）----
            min_style_val = p.get("min_style", "cg")
            etol = p.get("etol", "1.0e-6")
            ftol = p.get("ftol", "1.0e-8")
            maxiter = p.get("maxiter", 1000)
            maxeval = p.get("maxeval", 10000)

            lines.append(f"min_style       {min_style_val}")
            lines.append(f"minimize        {etol} {ftol} {maxiter} {maxeval}")

        lines.append("")
        lines.append('print           "Simulation complete"')
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get_diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(  # type: ignore[override]
        self,
        doc: DocumentModel,
        schema: SchemaRegistry | None = None,
    ) -> list[Diagnostic]:
        """基于规则对 LAMMPS 文档做静态诊断。

        检查规则：
        1. unknown_command      — 未识别的 LAMMPS 命令（warning）
        2. units_consistency    — pair_style 与 units 兼容性（info）
        3. missing_mass         — 有 create_atoms 但没有 mass（error）
        4. missing_pair_coeff   — 有 pair_style 但没有 pair_coeff（error）
        5. missing_pair_style   — 有 pair_coeff 但没有 pair_style（error）
        6. timestep_range       — timestep 值范围检查（warning）
        7. boundary_dimension   — dimension=2 时 boundary 第三分量应为 f（warning）
        8. fix_before_run       — 有 run 但没有 fix（MD 任务 warning）
        9. 追加 parse_errors
        """
        # 始终使用内部绑定的原始 registry（避免 _BoundRegistry 接口不兼容）
        self._schema

        diags: list[Diagnostic] = []

        # 构建命令存在性映射（命令名小写 → 最后一个 ParsedParam）
        present: dict[str, ParsedParam] = {}
        # 收集所有同名命令（fix 可以出现多次）
        all_cmds: dict[str, list[ParsedParam]] = {}
        for param in doc.params:
            name_lc = param.name.lower()
            present[name_lc] = param
            all_cmds.setdefault(name_lc, []).append(param)

        def _has(cmd: str) -> bool:
            return cmd.lower() in present

        def _get_value(cmd: str) -> str:
            p = present.get(cmd.lower())
            return str(p.value).strip() if p else ""

        # ---- 1. unknown_command ----
        for param in doc.params:
            name_lc = param.name.lower()
            if name_lc not in _KNOWN_COMMANDS:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=f"未识别的 LAMMPS 命令 '{param.name}'",
                        range=param.range,
                        param=param.name,
                        suggestion="检查命令名拼写，或查阅 LAMMPS 官方文档",
                        rule_id="unknown_command",
                    )
                )

        # ---- 2. units_consistency ----
        units_val = _get_value("units")
        pair_style_val = _get_value("pair_style")
        if units_val and pair_style_val:
            ps_lower = pair_style_val.lower()
            # EAM 势要求 metal units
            if ps_lower.startswith("eam") and units_val != "metal":
                diags.append(
                    Diagnostic(
                        severity="info",
                        message=(
                            f"pair_style eam 通常与 units metal 配合使用，"
                            f"当前 units = {units_val}"
                        ),
                        range=present.get("pair_style", present.get("units")).range,
                        param="pair_style",
                        suggestion="建议将 units 改为 metal，或改用 lj/cut 势",
                        rule_id="units_consistency",
                    )
                )

        # ---- 3. missing_mass ----
        if _has("create_atoms") and not _has("mass"):
            diags.append(
                Diagnostic(
                    severity="error",
                    message="使用了 create_atoms 但未定义 mass",
                    range=present["create_atoms"].range,
                    param="mass",
                    suggestion="在 create_atoms 之后添加：mass 1 <原子质量>",
                    rule_id="missing_mass",
                )
            )

        # ---- 4. missing_pair_coeff ----
        if _has("pair_style") and not _has("pair_coeff"):
            diags.append(
                Diagnostic(
                    severity="error",
                    message="定义了 pair_style 但没有对应的 pair_coeff",
                    range=present["pair_style"].range,
                    param="pair_coeff",
                    suggestion="在 pair_style 后添加 pair_coeff 命令设置势函数参数",
                    rule_id="missing_pair_coeff",
                )
            )

        # ---- 5. missing_pair_style ----
        if _has("pair_coeff") and not _has("pair_style"):
            diags.append(
                Diagnostic(
                    severity="error",
                    message="有 pair_coeff 但未定义 pair_style",
                    range=present["pair_coeff"].range,
                    param="pair_style",
                    suggestion="在 pair_coeff 之前添加 pair_style 命令",
                    rule_id="missing_pair_style",
                )
            )

        # ---- 6. timestep_range ----
        if _has("timestep"):
            ts_param = present["timestep"]
            ts_str = _get_value("timestep")
            try:
                ts_val = float(ts_str.split()[0])
                units_key = units_val if units_val in _TIMESTEP_RANGES else "lj"
                ts_min, ts_max = _TIMESTEP_RANGES[units_key]
                if ts_val < ts_min or ts_val > ts_max:
                    diags.append(
                        Diagnostic(
                            severity="warning",
                            message=(
                                f"timestep = {ts_val} 超出 units={units_key} 的合理范围 "
                                f"[{ts_min}, {ts_max}]"
                            ),
                            range=ts_param.range,
                            param="timestep",
                            suggestion=(
                                f"对于 units {units_key}，推荐 timestep 在 "
                                f"{ts_min} ~ {ts_max} 之间"
                            ),
                            rule_id="timestep_range",
                        )
                    )
            except (ValueError, IndexError):
                pass

        # ---- 7. boundary_dimension ----
        dim_str = _get_value("dimension")
        boundary_str = _get_value("boundary")
        if dim_str == "2" and boundary_str:
            parts = boundary_str.split()
            if len(parts) >= 3 and parts[2] not in ("f", "fs", "fm"):
                diags.append(
                    Diagnostic(
                        severity="warning",
                        message=(
                            f"dimension = 2 时 boundary 第三分量应为 'f'（固定边界），"
                            f"当前为 '{parts[2]}'"
                        ),
                        range=present["boundary"].range,
                        param="boundary",
                        suggestion="将 boundary 第三分量改为 'f'，如：boundary p p f",
                        rule_id="boundary_dimension",
                    )
                )

        # ---- 8. fix_before_run ----
        if _has("run") and not _has("fix"):
            diags.append(
                Diagnostic(
                    severity="warning",
                    message="有 run 命令但没有定义任何 fix（MD 积分器通常需要 fix nve/nvt/npt）",
                    range=present["run"].range,
                    param="fix",
                    suggestion=(
                        "添加积分器 fix，例如：\n" "fix  1 all nve\n" "run  10000"
                    ),
                    rule_id="fix_before_run",
                )
            )

        # ---- 9. 追加 parse_errors ----
        diags.extend(doc.parse_errors)

        return diags

    # ------------------------------------------------------------------
    # get_completions
    # ------------------------------------------------------------------

    def get_completions(  # type: ignore[override]
        self,
        doc: DocumentModel,
        line: int,
        col: int,
        schema: SchemaRegistry | None = None,
    ) -> list[CompletionItem]:
        """返回光标位置处的补全候选列表。

        策略：
        - 如果光标在行首（col == 0 或该行为空），返回所有顶级命令
        - 如果当前行是 pair_style，返回常见 pair_style 类型
        - 如果当前行是 units，返回 units 选项
        - 其他情况返回 Schema 中所有命令的补全
        """
        # 始终使用内部绑定的原始 registry
        _reg = self._schema

        # 找出光标行的内容
        raw_lines = doc.raw_text.splitlines()
        current_line_text = ""
        if 1 <= line <= len(raw_lines):
            current_line_text = _strip_comment(raw_lines[line - 1]).strip()

        tokens = current_line_text.split()
        items: list[CompletionItem] = []

        # 检查是否在值位置（行首有命令）
        if len(tokens) >= 1 and col > 0:
            cmd = tokens[0].lower()

            if cmd == "pair_style" and len(tokens) <= 1:
                # 提供 pair_style 类型补全
                for ps in _PAIR_STYLE_NAMES:
                    items.append(
                        CompletionItem(
                            label=ps,
                            detail="[pair_style]",
                            documentation=f"## `pair_style {ps}`\n\npair_style 类型: {ps}",
                            insert_text=ps,
                            category="potential",
                            sort_priority=0,
                        )
                    )
                return items

            if cmd == "units" and len(tokens) <= 1:
                # 提供 units 选项补全
                for u in _UNITS_OPTIONS:
                    items.append(
                        CompletionItem(
                            label=u,
                            detail="[units]",
                            documentation=f"## `units {u}`\n\nLAMMPS 单位制: {u}",
                            insert_text=u,
                            category="initialization",
                            sort_priority=0,
                        )
                    )
                return items

            if cmd == "min_style" and len(tokens) <= 1:
                # 提供 min_style 选项
                tag = _reg.get_tag("lammps", "min_style")
                enum_vals = tag.enum_values if tag else []
                for v in enum_vals or []:
                    items.append(
                        CompletionItem(
                            label=v,
                            detail="[min_style]",
                            documentation=f"## `min_style {v}`\n\n最小化算法: {v}",
                            insert_text=v,
                            category="run",
                            sort_priority=0,
                        )
                    )
                return items

            if cmd == "atom_style" and len(tokens) <= 1:
                tag = _reg.get_tag("lammps", "atom_style")
                enum_vals = tag.enum_values if tag else []
                for v in enum_vals or []:
                    items.append(
                        CompletionItem(
                            label=v,
                            detail="[atom_style]",
                            documentation=f"## `atom_style {v}`\n\n原子样式: {v}",
                            insert_text=v,
                            category="initialization",
                            sort_priority=0,
                        )
                    )
                return items

        # 行首或通用情况：返回所有命令补全
        # 已出现的命令名
        present_names: set[str] = {p.name.lower() for p in doc.params}

        # 上下文类别（光标附近 5 行）
        context_categories: set[str] = set()
        for param in doc.params:
            if abs(param.range.start_line - line) <= 5:
                tag = _reg.get_tag("lammps", param.name)
                if tag and tag.category:
                    context_categories.add(tag.category)

        all_tags = _reg.list_tags("lammps")
        for tag in all_tags:
            if tag.category in context_categories:
                priority = 0
            elif tag.name.lower() not in present_names:
                priority = 5
            else:
                priority = 10

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

        # 补充已知但不在 schema 中的常见命令
        schema_names = {t.name.lower() for t in all_tags}
        for cmd_name in sorted(_KNOWN_COMMANDS):
            if cmd_name not in schema_names:
                if cmd_name not in present_names:
                    priority = 8
                else:
                    priority = 12
                items.append(
                    CompletionItem(
                        label=cmd_name,
                        detail="[lammps command]",
                        documentation=f"## `{cmd_name}`\n\nLAMMPS 命令",
                        insert_text=f"{cmd_name} ",
                        category="general",
                        sort_priority=priority,
                    )
                )

        items.sort(key=lambda x: (x.sort_priority, x.label.lower()))
        return items
