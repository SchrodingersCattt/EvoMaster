# Plotting plugin 设计（Imagine 指南移植为 matmaster skill 族）

日期：2026-06-11
状态：已评审通过（设计对话中逐项确认）

## 1. 背景与目标

Claude Code 桌面端的 visualize 工具（自称 Imagine — Visual Creation Suite）以一份分模块的文本指南
驱动模型产出高质量可视化。本设计把这份指南的可移植部分改造成 matmaster 的 skill 族，
让 matmaster 的 agent 在现有交付通道上稳定产出合规的科研图。

硬前提（均已核实）：

- 交付通道唯一：Bash 沙箱产图文件 → AttachFigure 发布（仅收 .png/.jpg/.jpeg/.webp，
  文件头嗅探校验，批量 all-or-nothing，单次 ≤20 张）→ 正文以 `[[fig:<figure_id>]]` 引用。
  见 `matmaster/tools/builtin/attach_figure_tool.py`、`matmaster/tools/figure_artifacts.py:19`。
- 流式协议无 widget/HTML/SVG 渲染面，前端渲染 markdown 文本 + 图片 URL。不改协议、不改前端。
- 沙箱镜像（Dockerfile.remote）：matplotlib 3.10.8 为唯一绘图库；numpy/scipy/pandas/
  pymatgen/ase/matminer/pymupdf 在位；无 plotly/seaborn/graphviz/networkx；无 CJK 字体。
- skill 机制：SKILL.md + YAML frontmatter，元数据常驻上下文，正文经 SkillTool 按需取回；
  `${SKILL_DIR}`/`${PLUGIN_DIR}` 占位符在取回时替换（`matmaster/tools/builtin/skill_tool.py:92-96`）；
  agent 具备 read/grep/glob/bash/write 内置工具；`depends_on` 仅级联激活 MCP server，不注入正文。

## 2. 决策记录

| 决策点 | 结论 |
|---|---|
| 改造方向 | 适配现有通道，零前端/协议改动 |
| 覆盖场景 | 通用数据图表、材料领域图、流程/示意图、汇报组图（全部） |
| 组织形态 | 多 skill 拆分，构建为一个 plugin（plugin 目录承载共享资产） |
| 前端主题 | 亮色为主 → 图一律白底不透明，浅色友好配色 |
| 图内文字语言 | 跟随用户语言 → 镜像加 CJK 字体 |
| 流程/示意图实现 | 手写 SVG（遵循 Imagine 坐标纪律）→ 沙箱内栅格化 PNG → AttachFigure |
| data-analysis 职责重叠 | 暂不处理（其 description 声称可视化职责，已知事项） |

## 3. Plugin 结构

```
matmaster/plugins/plotting/
├── plugin.yaml
├── shared/
│   ├── style-contract.md   # 全局样式与交付契约，唯一权威源（~60 行）
│   ├── mm_style.py         # matplotlib 资产：九坡道色板常量、rcParams 一键应用、
│   │                       #   CJK 字体配置、白底 savefig 封装
│   ├── svg_prelude.txt     # SVG 模板头：arrow marker defs + 全内联属性节点范例
│   └── svg2png.py          # pymupdf 栅格化命令行（SVG → PNG，固定 DPI）
└── skills/
    ├── plot-diagram/
    │   ├── SKILL.md
    │   └── references/svg-discipline.md   # Imagine 坐标纪律移植全文
    ├── plot-chart/SKILL.md
    ├── plot-materials/SKILL.md
    └── plot-report/SKILL.md
```

共享机制：每个 SKILL.md 正文第一节硬性要求先读 `${PLUGIN_DIR}/shared/style-contract.md`；
Python/SVG 资产经 `${PLUGIN_DIR}` 绝对路径在沙箱内 import 或调用。样式规则单源，四个 skill 不抄写。
目录先例参照 `matmaster/plugins/atomic-structure-ops/`（多 skill plugin + skill 内 references/）。

## 4. 共享契约（style-contract.md）内容要点

交付契约：

- 图文件写入 workspace 绝对路径；AttachFigure 批量发布，失败即整批回滚，修路径后整批重发；
  成功后必须在正文用 `[[fig:id]]` 引用每张图。
- caption 语言跟随用户语言，一句话自包含（脱离正文可读懂图意）。位图无 DOM，
  Imagine 的无障碍语义（sr-only/aria）折算为 caption 质量要求。
- 表格不画成图片，走正文 markdown 表格（Imagine 原则直接保留）。
- 不发明数据：图中每个数据点来自真实计算结果或用户显式给定，绝不虚构坐标/数值
  （Imagine 地图章节"never invent coordinates"原则的推广形式）。

样式契约：

- 白底不透明：`savefig(..., facecolor='white', dpi=200, bbox_inches='tight')`。
- 九色坡道表（hex 硬编码，浅色三件套固定取法：50 填充 / 600 描边 / 800 标题 / 600 副标题）。
  颜色编码语义而非序号；每图 ≤2 坡道，颜色承载含义时配一行图例；gray 表中性/结构。
  通用类目优先 purple/teal/coral/pink，blue/green/amber/red 保留给信息/成功/警告/错误语义
  （illustrative 映射物理量时除外）。
- 字号双轨：标签 14px / 副文字 12px；句首大写（sentence case）；禁 emoji；
  颜色之外必须有第二视觉线索区分系列（linestyle/marker/hatch），图例同时展示两者。
- 显示数字取整到语境精度（计数整数、百分比 1-2 位小数）；负号在货币/单位符号之前。
- 图内文字语言跟随用户语言；CJK 经 mm_style.py 配置 rcParams 字体族。

## 5. 四个 skill 规格

| skill | 载体与管线 | 正文骨架 |
|---|---|---|
| plot-diagram | 手写 SVG → svg2png.py → AttachFigure | 图型路由（route on the verb）；复杂度预算；先读 references/svg-discipline.md 再动笔；svg_prelude.txt 起手 |
| plot-chart | matplotlib（import mm_style）→ PNG | 图型选型（何种数据用何种图）；轴/图例/网格规范；横条图高度 = n×0.4in + 定值；收敛/谱线惯用法 |
| plot-materials | pymatgen plotter 优先 + mm_style 调样式 | 能带（高对称点路径标注）、DOS（E−E_F 轴）、XRD（2θ）、RDF（g(r)）、相图各自惯例；先查 pymatgen 自带 plotter 再手画 |
| plot-report | matplotlib subplot_mosaic 组版 | 组版布局（汇总数字卡上行 + 主图下行）、对齐、标题层级、子图标号 (a)(b)(c) |

frontmatter：四个 skill 均不设 `mcp_server`、不设 `depends_on`。description 按触发质量撰写
（场景动词 + 对象 + 用户惯用语），是方案 C 命中粒度优势能否兑现的关键，实现时逐条推敲。

plot-diagram/references/svg-discipline.md 移植 Imagine 的全部坐标纪律：

- viewBox 安全清单（680 宽、负坐标禁止、底边 +40 缓冲、text-anchor=end 左溢检查）；
- 字宽估算：14px ≈ 8px/字符，CJK/特殊符号加宽 30-50%，框宽 = max(标题×8, 副标题×7) + 24；
- tier packing 先算后画（横排 ≤4 框，总宽预算核算示例）；
- 箭头相交检查与 L 弯绕行；`dominant-baseline="central"` 垂直对中公式；
- 连接线必须 `fill="none"`；0.5px 描边；rx=4 默认；箭头 marker defs 全文；
- 图型三分法与触发语对照表（flowchart / structural / illustrative，route on the verb）；
- 循环不画环：线性 SVG + ↻ 返回标注（Imagine 的 HTML stepper 路线无渲染面，取其自带降级方案）；
- 标签放置纪律（标签单侧集中、留 140px 边距、文字与任何笔画间 8px 净空）。

## 6. 内容映射清单（Imagine 全文 795 行 → 去向）

直接移植：

- 复杂度预算、九色坡道表与取色规则、SVG setup 与坐标纪律全部、图型路由表、
  组合图叙事原则（多图分次交付、图间必有衔接文字、承诺几张就交付几张）。

翻译适配：

- 预载 CSS class（t/ts/th/c-*/box/node/arr）→ 全内联属性 + svg_prelude.txt 模板。
  动因：MuPDF 对 SVG 内 CSS class 支持弱，且脱离宿主样式表后内联是唯一忠实译法。
- Anthropic Sans → 通用 sans-serif + 镜像 CJK 字体（字宽估算表近似仍适用）。
- Chart.js 规则 → matplotlib 语境重写：图例带数值且置图外、色+线型/marker 双编码、
  数字精度与负号格式、横条图高度公式。载体细节（canvas/CDN/UMD）全部不取。
- metric cards / dashboard layout → plot-report 的组版模式（无边框汇总数字轴 + 主图）。

砍除（无渲染面或与决策冲突）：

- CSS 变量与暗色模式（亮色已定）、流式输出规则、一切交互（sendPrompt/动画/控件/HTML stepper）、
  Tabler 图标、mermaid ERD、CDN/CSP 允许列表、sr-only（折算进 caption 规范）、
  Geographic maps（拓扑需联网获取，材料场景需求弱；保留其"绝不发明坐标"原则于 style-contract）。

净新增（Imagine 没有、matmaster 必需）：

- 交付契约全部（AttachFigure 语义、[[fig:id]] 引用、caption 规范）；
- 图内语言跟随用户 + CJK 字体链路；
- plot-materials 全部领域惯例（pymatgen plotter 优先策略、各谱图轴约定）；
- 白底 savefig 规范；svg2png 栅格化管线。

## 7. 配套工程

- Dockerfile.remote 加 `fonts-noto-cjk`（matplotlib 与 SVG 栅格化共用）。唯一镜像改动。
- data-analysis skill 不动；其 description 与 plot-* 的职责重叠为已知事项，后续另行处理。

## 8. 风险与验证顺序

最大技术风险：pymupdf 栅格化中文标签 + 全内联属性 SVG 的渲染质量（MuPDF 的 SVG 子集支持、
字体解析路径）。实现计划的第一项即此 spike：

1. 先完成第 7 节镜像字体改动（CJK 字形核对的前置条件）；
2. 手写一张含中文标签、坡道色、箭头 marker 的全内联属性 SVG；
3. 沙箱内 pymupdf 转 PNG，肉眼核对字形（无豆腐块）、marker、描边；
4. 失败则换 cairosvg（需 apt libcairo2 + pip cairosvg，仍零前端改动），spec 不变。

次级风险：方案 C 的跨 skill 一致性依赖"先读 shared 契约"的指令遵从。缓解：契约控制在 ~60 行
（读取成本低），且四个 SKILL.md 的第一节统一用同款强制措辞。

## 9. 验收

四个 skill 各跑一条人工端到端冒烟：触发命中 → 读取共享契约 → 沙箱产图 → AttachFigure 成功 →
正文 `[[fig:id]]` 引用成立；图面合规肉眼核对（白底、坡道色、14/12 双字号、句式大小写、
图例双编码）。不新增自动化测试。
