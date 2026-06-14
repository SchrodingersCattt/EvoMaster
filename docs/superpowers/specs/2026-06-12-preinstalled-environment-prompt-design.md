# System Prompt 预装环境段设计

- Date: 2026-06-12
- Status: Draft
- Author: Kealdoom + Claude
- 基线:
  - 当前 checkout: `matmaster-evo`, 分支 `codex/provider-stage1`
  - 动态 Environment 段已实现(2026-06-03 设计): workdir/platform/shell/OS/date 运行时注入
- 影响范围:
  - `matmaster/exps/_base.toml`(新增段)
  - `matmaster/context/environment.py`(删常量与三行渲染)
  - `tests/matmaster/context/test_environment.py`(删对应断言)
  - `Dockerfile.remote`(加同步注释)
  - 不改 `matmaster/context/system_prompt.py`、`matmaster/core/exp.py`

## 1. 背景

system prompt 目前不含执行镜像的软件清单。模型不知道:

- 哪些 Python 库 / CLI 工具已预装(ase、pymatgen、rdkit、vaspkit、LaTeX 工具链等)
- 哪些东西明确不在(VASP/CP2K/LAMMPS 等计算引擎本体)

后果: 对已预装的库浪费 turn 做 import 探测(Early termination guardrail 要求执行前验证
可用性); 对未安装的软件产生幻觉可用性(装了 vaspkit 容易诱导模型认为 VASP 可用)。

同时, 2026-06-03 设计的动态 Environment 段里 platform/shell/OS 三项是执行镜像的固定
常量, 与本次要加的库清单同源(都由 `Dockerfile.remote` 决定), 拆在动态注入里使"镜像
静态属性"分散两处。经确认: 镜像静态属性统一进 `_base.toml`, 动态注入只保留工作目录
与日期两条。

## 2. 当前事实

- `_base.toml` system_prompt 1027 token, 四个 exp 共享; static prompt 预算目标 12k /
  上限 15k(`tests/evaluation/test_exp_prompt_budget.py`), 当前最大 planner 总量 8561。
- 动态 Environment 段(`matmaster/context/environment.py`)渲染 5 行: workdir、platform、
  shell、OS、date。`EXECUTION_PLATFORM/SHELL/OS` 常量仅 environment.py 与其测试引用。
- Bash 工具描述已含 "The session workspace directory for this run is ..."
  (`matmaster/tools/builtin/bash_tool.py:92`), AttachFigure 同样携带 workspace 信息——
  workdir 在动态段与工具描述中冗余, 但保留无害。
- `Dockerfile.remote` 预装四类内容: 材料化学库(ase、pymatgen、dpdata、rdkit、packmol、
  molcrys-kit、mendeleev、matminer、cp2k-input-tools、openbabel、vaspkit)、数据/ML 库
  (numpy 2.x、scipy、pandas、scikit-learn、xgboost-cpu、lightgbm、scikit-image、pint)、
  文档绘图(matplotlib、openpyxl、python-docx、pymupdf、cairosvg、texlive+latexmk、
  Noto CJK 字体)、通用 CLI(requests、tmux、wget、curl、file、node/npm、lark)。

## 3. 目标与非目标

### 目标

- `_base.toml` 新增 Preinstalled environment 段: 镜像平台事实 + 按用途分组的预装清单 +
  负面空间声明。
- environment.py 动态段缩为 workdir + date 两行。
- 不破坏 prompt cache 与 static token 预算。

### 非目标

- 不引入运行时 pip list 注入(见第 7 节方案 B)。
- 不做清单与 Dockerfile 的 CI 一致性校验(注释互指足够, 见方案 C)。
- 不处理 `<scratchpad-dir-path>` 悬空占位符(沿袭 6/3 设计的非目标)。
- 不在 prompt 中写 pip install 补装政策(列表外软件由 Early termination guardrail 兜底:
  报告不可用, 由用户决定)。

## 4. 设计

### 4.1 `_base.toml` 新段

位置: `# Computational software` 之后。内容:

```markdown
# Preinstalled environment

Tool commands run on a remote Linux node: Ubuntu 24.04 (x86_64), Bash,
Python 3.12. Preinstalled and usable directly without availability checks:

- Materials & chemistry: ase, pymatgen, dpdata, rdkit, openbabel (`obabel`
  CLI), packmol, molcrys-kit, mendeleev, matminer, cp2k-input-tools,
  `vaspkit` CLI.
- Data & ML: numpy (2.x API), scipy, pandas, scikit-learn, xgboost
  (CPU-only), lightgbm, scikit-image, pint.
- Documents & plotting: matplotlib, openpyxl, python-docx, pymupdf,
  cairosvg; LaTeX toolchain (`latexmk`, pdflatex) and Noto CJK fonts
  (usable for Chinese labels in matplotlib and LaTeX).
- General: requests, tmux, wget, curl, file, node/npm, lark (Feishu CLI).

NOT preinstalled: computation engines themselves (VASP, CP2K, LAMMPS,
Gaussian, phonopy, ...). `vaspkit` is pre/post-processing only — it cannot
run VASP. For anything not listed here, the Early termination guardrail
applies: verify availability before relying on it.
```

要点:

- 版本只标 Python 3.12 与 numpy 2.x(影响 API 行为: numpy 2.x 移除了 `np.float_` 等
  1.x API); 其余不写版本, 避免镜像重建时清单漂移。
- 负面空间是该段最大价值: 明确计算引擎本体不在环境内, vaspkit 仅前后处理。
- 与 Early termination 衔接: 清单内免验证(省探测 turn), 清单外照旧先验证。

### 4.2 environment.py 瘦身

删 `EXECUTION_PLATFORM/SHELL/OS` 三常量与对应渲染行。函数签名不变:
`build_environment_section(*, execution_workdir: str, now: datetime)`。渲染:

```text
You have been invoked in the following environment:
 - Working directory: {execution_workdir}
 - Today is {YYYY-MM-DD} ({tz_label}).
```

动态段标题沿用 "Environment"(`system_prompt.py` 零改动), 与静态段标题
"Preinstalled environment" 可区分: 前者是会话上下文(你在哪、今天几号), 后者是镜像
预装内容。

### 4.3 `Dockerfile.remote` 同步注释

文件头部加一行注释: 修改预装清单时同步更新 `matmaster/exps/_base.toml` 的
Preinstalled environment 段。

## 5. 缓存与 token 预算分析

- 静态段约 +230 token: planner 8561 → 约 8800, 低于 12k 目标; 预算测试自动把关,
  无需新增检查。
- 动态段缩为两行, 会话内稳定性不变(workdir session 内不变、日期到天), 缓存行为不变;
  镜像常量挪进静态缓存前缀, 缓存复用严格不劣化。

## 6. 测试

- 修改现有 `tests/matmaster/context/test_environment.py`: 删 platform/shell/OS 断言与
  `EXECUTION_*` import; workdir、日期、时区、纯函数断言保持。
- 不新增测试文件。`test_exp_prompt_budget.py` 与 `test_system_prompt_builder.py`
  不受影响。

## 7. 方案对比与取舍记录

| 方案 | 描述 | 结论 |
|------|------|------|
| A(选用) | `_base.toml` 手写精选分组清单 + 镜像平台事实 | 纯静态属性进缓存前缀, 零代码机制, 可表达分组与负面空间 |
| B | 运行时注入 pip list | 传递依赖噪音大、无语义分组、给不了负面空间; 静态信息不需要动态机制 |
| C | A + CI 一致性校验(解析 Dockerfile pip 块比对) | 镜像清单精确 pin、低频变更, 注释互指足够, 暂不加机器 |

其它已确认取舍:

- workdir 保留在动态段(用户决定): Bash 工具描述虽已携带同一信息, 冗余无害, 移除收益小。
- kernel 信息丢弃: 旧常量中的 "kernel 5.10.134-18.0.10.lifsea8.x86_64" 是宿主节点属性
  而非镜像属性(容器共享宿主内核), `Dockerfile.remote` 不定义它, 对模型生成命令无用。
- local session 下静态段描述的是远程镜像, 本地不准确——沿袭 6/3 设计已接受的取舍
  (不需要 local 准确性)。
- 动态段标题不改名: 曾考虑改 "Current date", 因保留 workdir 后名不副实, 且保持
  "Environment" 使 `system_prompt.py`/`exp.py` 零改动。
