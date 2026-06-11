# Plotting Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Imagine 可视化指南的可移植部分落成 matmaster 的 plotting plugin（4 个 skill + 共享资产），让 agent 经 Bash 产图 → AttachFigure 发布 → `[[fig:id]]` 引用的现有通道稳定产出合规科研图。

**Architecture:** 单 plugin 多 skill（参照 `matmaster/plugins/atomic-structure-ops/` 先例）。样式与交付规则单源于 `shared/style-contract.md`；matplotlib 资产 `shared/mm_style.py` 沙箱内 `sys.path` 导入；手写 SVG 经 `shared/svg2png.py`（cairosvg）栅格化为 PNG。`${PLUGIN_DIR}`/`${SKILL_DIR}` 由 SkillTool 取回时替换为（远端映射后的）绝对路径（`matmaster/tools/builtin/skill_tool.py:92-96`）。

**Tech Stack:** SKILL.md + plugin.yaml（`matmaster/skills/registry.py` 自动发现）、matplotlib 3.10.8、cairosvg ≥2.7、pymatgen plotter、fonts-noto-cjk。

**Spec:** `docs/superpowers/specs/2026-06-11-plotting-plugin-design.md`（含 Imagine 指南参考文本，§10 第 161 行 JSON 转义）。

---

## 执行环境

- 基分支：`codex/provider-stage1`（spec 仅存在于此分支）。新建分支 `plotting-plugin`。
- worktree 中跑 Python 验证前先软链 venv：`ln -s /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.venv .venv`。
- 本地 venv 已有 matplotlib 3.10.8（与沙箱镜像同版本）+ cairosvg（spike 时装入）。**2026-06-11 spike 结论：pymupdf 1.27.1 的 SVG 管线不渲染 `<marker>`、忽略 `stroke-dasharray`、不支持 `dominant-baseline`、不从 viewBox 推断页高——按 spec §8.4 预授权路径切换 cairosvg。** cairosvg 的几何特性（marker/虚线/圆角/viewBox 尺寸推断）已本地验证通过；SVG 内 CJK 字形的最终核对依赖 Linux fontconfig + fonts-noto-cjk，属沙箱 E2E 验收项（本地 macOS quartz 后端无 Noto CJK，豆腐块为环境局限而非缺陷）。matplotlib 的 CJK 在本地经 PingFang 回退验证、沙箱经 Noto 命中。对数轴指数（mathtext `\mathdefault`）只取字体链首个字体且不逐字形回退：沙箱 Noto Sans CJK SC 自带 U+2212 故正确；本地 PingFang 链首会把指数负号渲染成占位符，与 SVG CJK 同属本地环境局限（实测 DejaVu 置首可修负号但 CJK 变豆腐块，不可兼得，故不改链序）。
- pre-commit 钩子会跑 black（88 列）/isort/flake8/pyupgrade/end-of-file-fixer/trailing-whitespace/check-yaml。若钩子自动改文件，`git add -u` 后重新提交即可。py 文件 ≤1000 行（远低于）。不给 `mm_style.py`/`svg2png.py` 加 shebang 或可执行位（避开 shebang 钩子）。
- 全程**不新增任何自动化测试**（spec §9），验证一律用一次性命令 + Read 工具肉眼核对图片。

## File Structure

| 文件 | 职责 |
|---|---|
| `Dockerfile.remote` | 修改：apt 增加 `fonts-noto-cjk`、`libcairo2`，pip 增加 `cairosvg`（spike 后扩展，spec §8.4） |
| `matmaster/plugins/plotting/plugin.yaml` | 瘦清单：name/category/description |
| `matmaster/plugins/plotting/shared/style-contract.md` | 全局交付+样式契约，唯一权威源（~65 行），四个 skill 第一节强制先读 |
| `matmaster/plugins/plotting/shared/mm_style.py` | 九坡道色板常量、rcParams 一键应用（含 CJK 字体链）、白底 savefig 封装、横条图高度公式 |
| `matmaster/plugins/plotting/shared/svg_prelude.txt` | SVG 起手模板：白底 rect + 三色箭头 marker defs + 全内联属性示例块（示例块兼作 spike 素材） |
| `matmaster/plugins/plotting/shared/svg2png.py` | cairosvg 栅格化命令行（SVG → 不透明 PNG，定 DPI） |
| `matmaster/plugins/plotting/skills/plot-diagram/SKILL.md` | 流程/结构/示意图 skill：路由、预算、管线 |
| `matmaster/plugins/plotting/skills/plot-diagram/references/svg-discipline.md` | Imagine 坐标纪律移植全文（内联属性化适配） |
| `matmaster/plugins/plotting/skills/plot-chart/SKILL.md` | 通用数据图表 skill |
| `matmaster/plugins/plotting/skills/plot-materials/SKILL.md` | 材料领域图 skill（pymatgen plotter 优先） |
| `matmaster/plugins/plotting/skills/plot-report/SKILL.md` | 汇报组图 skill（subplot_mosaic 组版） |

不动的文件：`matmaster/skills/data-analysis/SKILL.md`（职责重叠为已知事项，spec §2/§7）；前端、流式协议、`attach_figure_tool.py` 零改动。

---

### Task 1: 镜像 CJK 字体

**Files:**
- Modify: `Dockerfile.remote:15`（apt-get install 列表）

- [ ] **Step 1: 创建分支**

```bash
git checkout -b plotting-plugin codex/provider-stage1
```

- [ ] **Step 2: 在 apt 列表加 fonts-noto-cjk**

`Dockerfile.remote` 中：

```dockerfile
    file \
    openbabel \
```

改为：

```dockerfile
    file \
    fonts-noto-cjk \
    openbabel \
```

- [ ] **Step 3: 验证 Dockerfile 语法未破坏**

Run: `grep -n "fonts-noto-cjk" Dockerfile.remote`
Expected: 一行命中，行尾有 ` \`，位于 `file \` 与 `openbabel \` 之间。

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.remote
git commit -m "chore(remote-image): add fonts-noto-cjk for figure CJK text"
```

---

### Task 2: Plugin 清单 + 栅格化管线资产 + spike（风险闸门）

spec §8 规定的最大技术风险验证：渲染「中文标签 + 坡道色 + 箭头 marker + 全内联属性」SVG 的质量。prelude 的示例块本身就是 spike 素材。

> **2026-06-11 第一轮 spike 记录**：pymupdf 1.27.1 不渲染 `<marker>`（管线未实现）、忽略 `stroke-dasharray`、不支持 `dominant-baseline`（文字上浮 ~5px）、根 svg 无 height 时回落 US Letter 页高；中文与坡道色正常。按 spec §8.4 预授权切换 cairosvg（apt `libcairo2` + pip `cairosvg`），本任务以下内容为 cairosvg 版。

**Files:**
- Modify: `Dockerfile.remote`（apt 加 `libcairo2`，pip 列表加 `cairosvg`）
- Create: `matmaster/plugins/plotting/plugin.yaml`
- Create: `matmaster/plugins/plotting/shared/svg2png.py`
- Create: `matmaster/plugins/plotting/shared/svg_prelude.txt`

- [ ] **Step 0: Dockerfile.remote 加 cairosvg 依赖并单独提交**

apt 列表 `fonts-noto-cjk \` 后加一行 `    libcairo2 \`；pip install 列表（`"pint>=0.24"` 前）加一行 `    "cairosvg>=2.7" \`。

```bash
git add Dockerfile.remote
git commit -m "chore(remote-image): add cairosvg deps for svg rasterization"
```

- [ ] **Step 1: 写 plugin.yaml**

```yaml
name: plotting
category: reporting
description: "Publication-quality answer figures over the Bash + AttachFigure channel: general data charts (plot-chart), materials-convention plots (plot-materials), hand-written SVG diagrams rasterized to PNG (plot-diagram), and multi-panel report compositions (plot-report). Shared style contract and matplotlib/SVG assets live in shared/."
```

- [ ] **Step 2: 写 shared/svg2png.py**

```python
"""Rasterize a hand-written SVG to an opaque PNG via cairosvg.

Usage: python3 svg2png.py input.svg output.png [dpi]

Fixed-DPI rasterization for the plotting plugin's diagram pipeline. SVG user
units are CSS px (96/inch), so scale = dpi/96. background_color flattens
anything left transparent onto white on top of the prelude's background rect.
"""

from __future__ import annotations

import os
import sys

import cairosvg
from PIL import Image

DEFAULT_DPI = 200


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: python3 svg2png.py input.svg output.png [dpi]")
        return 2
    src, dst = argv[1], argv[2]
    if not os.path.exists(src):
        print(f"input not found: {src}")
        return 2
    dpi = int(argv[3]) if len(argv) == 4 else DEFAULT_DPI
    cairosvg.svg2png(
        url=src,
        write_to=dst,
        scale=dpi / 96,
        background_color="white",
    )
    with Image.open(dst) as im:
        width, height = im.size
    print(f"wrote {dst} ({width}x{height} px at {dpi} dpi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 3: 写 shared/svg_prelude.txt**

三处 `REPLACE_HEIGHT` 占位（根 height、viewBox、底色 rect——显式 height 让任何渲染器都不必从 viewBox 推断画布尺寸）；示例块覆盖：双行节点（紫坡道三件套 + 中文标题）、单行中性节点（灰）、直连箭头、虚线 leader + 特殊字符标签、L 弯绕行箭头。marker 用固定颜色（cairosvg 是 SVG 1.1 渲染器，不支持 SVG2 的 context-stroke），orient 用 `auto`（只用 marker-end，够用且稳）。字体族首选 Noto Sans CJK SC（沙箱 fontconfig 直接命中），逗号回退 sans-serif。

```
<svg width="680" height="REPLACE_HEIGHT" viewBox="0 0 680 REPLACE_HEIGHT" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow-gray" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#5F5E5A" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
    <marker id="arrow-purple" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#534AB7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
    <marker id="arrow-teal" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#0F6E56" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <rect x="0" y="0" width="680" height="REPLACE_HEIGHT" fill="#FFFFFF"/>
  <!-- EXAMPLE BLOCK: delete the example elements below this comment before drawing your own diagram, but keep the closing </svg> on the last line. -->
  <!-- Two-line node, purple trio: 50 fill / 600 stroke / 800 title / 600 subtitle -->
  <g>
    <rect x="60" y="40" width="200" height="56" rx="4" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
    <text x="160" y="58" text-anchor="middle" dominant-baseline="central" font-family="Noto Sans CJK SC, sans-serif" font-size="14" font-weight="500" fill="#3C3489">数据预处理</text>
    <text x="160" y="78" text-anchor="middle" dominant-baseline="central" font-family="Noto Sans CJK SC, sans-serif" font-size="12" fill="#534AB7">Clean and normalize</text>
  </g>
  <!-- Single-line neutral node, gray trio -->
  <g>
    <rect x="380" y="46" width="160" height="44" rx="4" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
    <text x="460" y="68" text-anchor="middle" dominant-baseline="central" font-family="Noto Sans CJK SC, sans-serif" font-size="14" font-weight="500" fill="#444441">Model fit</text>
  </g>
  <!-- Straight connector, head stops 10px before the target edge -->
  <line x1="260" y1="68" x2="370" y2="68" stroke="#5F5E5A" stroke-width="1" fill="none" marker-end="url(#arrow-gray)"/>
  <!-- Dashed leader line + margin label with special glyphs -->
  <line x1="540" y1="68" x2="566" y2="116" stroke="#888780" stroke-width="0.5" stroke-dasharray="3 3" fill="none"/>
  <text x="572" y="120" font-family="Noto Sans CJK SC, sans-serif" font-size="12" fill="#5F5E5A">R² = 0.98</text>
  <!-- L-bend detour connector -->
  <path d="M160 96 L160 150 L460 150 L460 100" stroke="#534AB7" stroke-width="1" fill="none" marker-end="url(#arrow-purple)"/>
</svg>
```

- [ ] **Step 4: 跑 spike——栅格化 prelude 示例**

```bash
sed 's/REPLACE_HEIGHT/200/g' matmaster/plugins/plotting/shared/svg_prelude.txt > /tmp/plot_spike.svg
.venv/bin/python matmaster/plugins/plotting/shared/svg2png.py /tmp/plot_spike.svg /tmp/plot_spike.png
```

Expected: `wrote /tmp/plot_spike.png (1417x417 px at 200 dpi)`（680×200 CSS px × 200/96，向上取整）。

- [ ] **Step 5: Read 工具查看 /tmp/plot_spike.png，逐项肉眼核对**

1. 背景白色不透明；
2. 文字布局正确：中文「数据预处理」在本地 macOS 预期为豆腐块（quartz 后端无 Noto CJK，环境局限不算失败），但占位与行位置须正常；沙箱 Linux 经 fontconfig 命中 Noto，字形核对属 E2E 验收项；
3. `R²` 上标字符正常；
4. 两个箭头都有箭头头、方向正确（一个向右、一个向上）；
5. leader 虚线成段；
6. 圆角 rx=4 可见；
7. 节点标题/副标题在框内垂直居中（不上浮贴顶）；
8. 颜色与坡道值一致（紫 50 填充紫 600 描边等，建议程序化取像素核对）。

**闸门规则（cairosvg 版）：**
- 第 4 项失败（marker 不渲染）或第 5 项失败（虚线不渲染）→ **停止执行**，报告用户（此后没有进一步预授权后备）。
- 仅第 7 项失败（dominant-baseline 不被支持，文字整体上浮约 4-5px）→ 不停止：删除 prelude 示例中三处 `dominant-baseline="central"`，把三个 `<text>` 的 y 改为槽位中心+5（58→63、78→83、68→73），重跑 Step 4-5 确认居中；并记录此结果，Task 5 的 svg-discipline.md §5 须采用「替代文案 B」（任务内已给出两版文案）。
- **2026-06-11 实测：8 项全过，spike 结论=文案 A**（dominant-baseline 偏移 -1.8px，属无降部字串固有偏移，非失败模式）。

- [ ] **Step 6: Commit**

```bash
git add matmaster/plugins/plotting/plugin.yaml matmaster/plugins/plotting/shared/svg2png.py matmaster/plugins/plotting/shared/svg_prelude.txt
git commit -m "feat(plugins): scaffold plotting plugin with svg rasterization assets"
```

---

### Task 3: 样式与交付契约（唯一权威源）

**Files:**
- Create: `matmaster/plugins/plotting/shared/style-contract.md`

- [ ] **Step 1: 写 style-contract.md（全文如下）**

````markdown
# Plotting style & delivery contract

Single source of truth for every plot-* skill. Read this before producing any
figure. Skills add domain rules on top; nothing below is repeated there.

## Delivery

- Generate figure files with Bash inside the session workspace, absolute
  paths. Final formats: .png (default), .jpg/.jpeg, .webp. Never deliver SVG
  or PDF as the published figure.
- Publish with one AttachFigure call per answer batch. Publishing is
  all-or-nothing: if any path is rejected nothing is published — fix the
  failing path and resend the whole batch.
- After a successful AttachFigure call, reference every figure in the answer
  body with its [[fig:<figure_id>]] marker. Published but unreferenced is a
  bug; promised in prose but not attached is a bug.
- Caption: one sentence, self-contained (readable without the answer text),
  in the user's language. The caption carries the accessibility burden of a
  bitmap — name what is shown, the encoding, and the one takeaway.
- Multi-figure answers are a narrative: figures appear in the order the text
  discusses them, with connecting prose between figure references — never a
  wall of consecutive markers.
- Tables are never images. Tabular results go in markdown tables in the
  answer body.
- Never invent data: every plotted point comes from a real computed result or
  a value the user explicitly provided. No illustrative fake numbers, no
  guessed coordinates, no "typical" curves. If the data is missing, say what
  must be computed instead of plotting.

## Style

- White opaque background, always:
  `fig.savefig(path, facecolor="white", dpi=200, bbox_inches="tight")` — or
  `mm_style.save_figure(fig, path)`, which does exactly this. SVG sources get
  the same via the prelude's white background rect.
- All color comes from the nine-ramp palette (constants in `mm_style.py`).
  Light-theme trio per ramp: 50 fill / 600 stroke and lines / 800 title text /
  600 secondary text. The text stops govern text on or labeling that ramp's
  colored elements; default chart text (titles, axis labels, ticks) stays
  matplotlib's near-black.

| Ramp | 50 | 100 | 200 | 400 | 600 | 800 | 900 |
|---|---|---|---|---|---|---|---|
| purple | #EEEDFE | #CECBF6 | #AFA9EC | #7F77DD | #534AB7 | #3C3489 | #26215C |
| teal | #E1F5EE | #9FE1CB | #5DCAA5 | #1D9E75 | #0F6E56 | #085041 | #04342C |
| coral | #FAECE7 | #F5C4B3 | #F0997B | #D85A30 | #993C1D | #712B13 | #4A1B0C |
| pink | #FBEAF0 | #F4C0D1 | #ED93B1 | #D4537E | #993556 | #72243E | #4B1528 |
| gray | #F1EFE8 | #D3D1C7 | #B4B2A9 | #888780 | #5F5E5A | #444441 | #2C2C2A |
| blue | #E6F1FB | #B5D4F4 | #85B7EB | #378ADD | #185FA5 | #0C447C | #042C53 |
| green | #EAF3DE | #C0DD97 | #97C459 | #639922 | #3B6D11 | #27500A | #173404 |
| amber | #FAEEDA | #FAC775 | #EF9F27 | #BA7517 | #854F0B | #633806 | #412402 |
| red | #FCEBEB | #F7C1C1 | #F09595 | #E24B4A | #A32D2D | #791F1F | #501313 |

- Color encodes meaning, not sequence: the same category keeps the same ramp
  everywhere; gray marks neutral/structural elements. When color carries
  meaning, add a one-line legend.
- Ramp budget: ≤2 ramps per figure (gray excluded) when color groups
  categories — diagrams, fills, grouped bars. Multi-series line charts may
  take successive ramps from `mm_style.CATEGORY_ORDER`, one per series, each
  paired with its second cue.
- Prefer purple/teal/coral/pink for generic categories. Reserve
  blue/green/amber/red for genuine info/success/warning/error semantics —
  except illustrative figures mapping physical quantities (temperature,
  pressure, energy), which may use warm/cool ramps freely.
- Never separate series by color alone: pair every color with a second cue
  (linestyle, marker, or hatch) and show both in the legend.
- Two label text sizes only: 14 for axis/node labels and titles, 12 for
  secondary text (ticks, legends, annotations, subtitles). Headline numbers
  on metric cards are data marks, not labels, and may go larger. Sentence
  case for Latin-script text. No emoji.
- Display numbers at context precision: counts as integers, percentages with
  1-2 decimals. The minus sign precedes any currency/unit symbol
  (−$5, not $−5).
- In-figure text language follows the user's language. CJK works via
  `mm_style.apply()` (matplotlib) or the prelude's font setup (SVG).
````

- [ ] **Step 2: 验证行数与表完整性**

Run: `wc -l matmaster/plugins/plotting/shared/style-contract.md && grep -c "^| " matmaster/plugins/plotting/shared/style-contract.md`
Expected: 总行数 ~72（表 11 行 = 表头 1 + 分隔 1 + 九坡道 9）；`^| ` 计数 10（分隔行 `|---|` 不匹配该模式）。

- [ ] **Step 3: Commit**

```bash
git add matmaster/plugins/plotting/shared/style-contract.md
git commit -m "feat(plugins): add plotting style and delivery contract"
```

---

### Task 4: matplotlib 样式资产 mm_style.py

**Files:**
- Create: `matmaster/plugins/plotting/shared/mm_style.py`

- [ ] **Step 1: 写 mm_style.py（全文如下）**

```python
"""Matplotlib styling assets for the plotting plugin.

Import from the plugin's shared directory inside the sandbox:

    import sys

    sys.path.insert(0, "<plugin dir>/shared")  # skills give the real path
    import mm_style

    mm_style.apply()

All colors come from the nine-ramp palette in style-contract.md (the single
source of truth). Light-theme trio: 50 fill / 600 stroke and series lines /
800 title text / 600 secondary text.
"""

from __future__ import annotations

import matplotlib
from cycler import cycler

STOPS = (50, 100, 200, 400, 600, 800, 900)

RAMPS: dict[str, tuple[str, ...]] = {
    "purple": (
        "#EEEDFE",
        "#CECBF6",
        "#AFA9EC",
        "#7F77DD",
        "#534AB7",
        "#3C3489",
        "#26215C",
    ),
    "teal": (
        "#E1F5EE",
        "#9FE1CB",
        "#5DCAA5",
        "#1D9E75",
        "#0F6E56",
        "#085041",
        "#04342C",
    ),
    "coral": (
        "#FAECE7",
        "#F5C4B3",
        "#F0997B",
        "#D85A30",
        "#993C1D",
        "#712B13",
        "#4A1B0C",
    ),
    "pink": (
        "#FBEAF0",
        "#F4C0D1",
        "#ED93B1",
        "#D4537E",
        "#993556",
        "#72243E",
        "#4B1528",
    ),
    "gray": (
        "#F1EFE8",
        "#D3D1C7",
        "#B4B2A9",
        "#888780",
        "#5F5E5A",
        "#444441",
        "#2C2C2A",
    ),
    "blue": (
        "#E6F1FB",
        "#B5D4F4",
        "#85B7EB",
        "#378ADD",
        "#185FA5",
        "#0C447C",
        "#042C53",
    ),
    "green": (
        "#EAF3DE",
        "#C0DD97",
        "#97C459",
        "#639922",
        "#3B6D11",
        "#27500A",
        "#173404",
    ),
    "amber": (
        "#FAEEDA",
        "#FAC775",
        "#EF9F27",
        "#BA7517",
        "#854F0B",
        "#633806",
        "#412402",
    ),
    "red": (
        "#FCEBEB",
        "#F7C1C1",
        "#F09595",
        "#E24B4A",
        "#A32D2D",
        "#791F1F",
        "#501313",
    ),
}

# Preferred order for generic categorical series; blue/green/amber/red are
# reserved for info/success/warning/error semantics (style-contract.md).
CATEGORY_ORDER = ("purple", "teal", "coral", "pink")

_LINESTYLES = ("-", "--", "-.", ":")

# Noto hits first in the sandbox image; the rest cover local dev machines.
CJK_FONT_STACK = [
    "Noto Sans CJK SC",
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "DejaVu Sans",
]


def stop(ramp: str, value: int) -> str:
    return RAMPS[ramp][STOPS.index(value)]


def fill(ramp: str) -> str:
    return stop(ramp, 50)


def stroke(ramp: str) -> str:
    return stop(ramp, 600)


def title_color(ramp: str) -> str:
    return stop(ramp, 800)


def hbar_figsize(n_bars: int, width: float = 8.0) -> tuple[float, float]:
    """Horizontal bar chart size: height = n x 0.4 in + fixed margins."""
    return (width, n_bars * 0.4 + 1.2)


def restyle(ax, width: float = 8.0, height: float = 5.0):
    """Re-impose contract text sizes on an Axes that a plotter restyled.

    pymatgen plotters route through pretty_plot, which overrides rcParams
    with 30-48pt text and its own figure size after apply() ran. Call this
    on the returned Axes, recolor lines as needed, then save_figure.
    """
    fig = ax.figure
    fig.set_size_inches(width, height)
    ax.title.set_size(14.0)
    for axis_label in (ax.xaxis.label, ax.yaxis.label):
        axis_label.set_size(14.0)
    ax.tick_params(axis="both", which="both", labelsize=12.0)
    legend = ax.get_legend()
    if legend is not None:
        for text in legend.get_texts():
            text.set_size(12.0)
    for text in ax.texts:
        text.set_size(12.0)
    return ax


def apply() -> None:
    """Apply the plugin rcParams: CJK-capable fonts, sizes, palette, grid."""
    rc = matplotlib.rcParams
    rc["font.family"] = "sans-serif"
    rc["font.sans-serif"] = CJK_FONT_STACK
    rc["axes.unicode_minus"] = False
    rc["font.size"] = 12.0
    rc["axes.titlesize"] = 14.0
    rc["axes.titleweight"] = "medium"
    rc["axes.labelsize"] = 14.0
    rc["xtick.labelsize"] = 12.0
    rc["ytick.labelsize"] = 12.0
    rc["legend.fontsize"] = 12.0
    rc["figure.titlesize"] = 14.0
    rc["figure.titleweight"] = "medium"
    rc["axes.prop_cycle"] = cycler(
        color=[stroke(ramp) for ramp in CATEGORY_ORDER]
    ) + cycler(linestyle=list(_LINESTYLES))
    rc["lines.linewidth"] = 1.8
    rc["axes.grid"] = True
    rc["grid.color"] = stop("gray", 200)
    rc["grid.linewidth"] = 0.5
    rc["axes.spines.top"] = False
    rc["axes.spines.right"] = False
    rc["axes.edgecolor"] = stop("gray", 600)
    rc["axes.linewidth"] = 0.8
    rc["legend.frameon"] = False
    rc["figure.facecolor"] = "white"
    rc["axes.facecolor"] = "white"
    rc["savefig.facecolor"] = "white"
    rc["savefig.dpi"] = 200


def save_figure(fig, path: str) -> str:
    """Save with the contract's mandatory white opaque background settings."""
    fig.savefig(path, facecolor="white", dpi=200, bbox_inches="tight")
    return path
```

- [ ] **Step 2: 本地渲染核验**

```bash
.venv/bin/python - <<'EOF'
import sys

sys.path.insert(0, "matmaster/plugins/plotting/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
fig, ax = plt.subplots(figsize=(6, 4))
for i in range(4):
    ax.plot([0, 1, 2], [i, i + 1, i * 2 - 1], label=f"系列{i}")
ax.set_xlabel("步数")
ax.set_ylabel("能量 (eV)")
ax.set_title("收敛测试")
ax.legend()
print(mm_style.save_figure(fig, "/tmp/mm_style_check.png"))
EOF
```

Expected: 输出 `/tmp/mm_style_check.png`，无 traceback。

- [ ] **Step 3: Read 工具查看 /tmp/mm_style_check.png，肉眼核对**

1. 白底；2. 四条线颜色为紫/青/珊瑚/粉 600 档且线型各异（实/虚/点划/点）；3. 中文轴标签与标题正常（本地经 PingFang 回退）；4. 负值刻度的减号正常显示；5. 无上/右脊柱，网格浅灰。

- [ ] **Step 4: Commit**

```bash
git add matmaster/plugins/plotting/shared/mm_style.py
git commit -m "feat(plugins): add mm_style matplotlib assets"
```

---

### Task 5: plot-diagram skill + SVG 坐标纪律

**Files:**
- Create: `matmaster/plugins/plotting/skills/plot-diagram/SKILL.md`
- Create: `matmaster/plugins/plotting/skills/plot-diagram/references/svg-discipline.md`

- [ ] **Step 1: 写 SKILL.md（全文如下）**

````markdown
---
name: plot-diagram
description: "Hand-draw flowcharts, architecture and structure diagrams, and mechanism schematics as SVG rasterized to PNG answer figures. Use when the user asks to draw or illustrate a workflow, process, pipeline, architecture, containment, or how a mechanism works — shapes and labels, not plotted data arrays."
---

# Plot Diagram

Produce reference diagrams (flowcharts, structural) and intuition diagrams
(illustrative schematics) as hand-written SVG, rasterized to PNG and published
with AttachFigure.

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Step 1 — route and budget

Read `${SKILL_DIR}/references/svg-discipline.md` in full before writing any
SVG element — it carries the figure-type routing table (§8) and every
coordinate rule. Then decide:

1. Figure type from the user's VERB, not the noun: flowchart / structural /
   illustrative (discipline §8).
2. Complexity budget: ≤4-5 nodes per figure, box subtitles ≤5 words, ≤2 color
   ramps plus gray. A request naming 6+ components becomes a stripped
   overview figure plus one figure per sub-flow, delivered as a narrative
   with connecting prose (contract rules).

## Step 2 — write the SVG

- Start from `${PLUGIN_DIR}/shared/svg_prelude.txt`: copy it into the
  workspace as `<name>.svg`, replace all three REPLACE_HEIGHT tokens with
  the computed height, and delete the example block (keep the closing
  </svg>).
- Inline presentation attributes only — every element carries its own
  font-family/font-size/fill/stroke. No class, no <style>, no CSS: the
  rasterizer supports a narrow SVG subset and silently drops styling it does
  not understand.
- Compute every coordinate before drawing (text widths → box widths → tier
  packing → connector routes), then run the discipline §2 checklist over the
  finished file.

## Step 3 — rasterize and self-check

```bash
python3 ${PLUGIN_DIR}/shared/svg2png.py /abs/workspace/<name>.svg /abs/workspace/<name>.png
```

View the PNG with the Read tool before attaching. Fix the SVG and re-rasterize
if any of these appear: missing glyphs (tofu boxes — usually the declared font
lacks that glyph; swap it for a supported character or plain text), text
overflowing a box or touching a stroke, absent or misrotated arrowheads,
content clipped at an edge. Iterate until clean — never attach an unchecked
figure.

## Step 4 — deliver

One AttachFigure call for the answer's figures (all-or-nothing batch), then
reference each as [[fig:<figure_id>]] with connecting prose between figures
(contract rules).

## Channel limits

The figure is a static PNG: no interactivity, no animation, no click-through,
no hover, no steppers. Sequence is expressed as several figures with prose
between; state change as before/after panels; cycles as a linear stage row
plus a "↻ returns to …" note — never a ring layout (discipline §6).
````

- [ ] **Step 2: 写 references/svg-discipline.md（全文如下；§5 的 dominant-baseline 条目按 Task 2 spike 结果二选一，两版均在下方给出）**

````markdown
# SVG discipline — coordinate rules for hand-written diagrams

Ported from the Imagine visual-creation guide, adapted to this channel:
static PNG via `svg2png.py` (cairosvg), white opaque background, inline
presentation attributes only. No CSS classes, no `<style>` blocks, no
interactivity, no animation, no dark mode. Read fully before writing any SVG.

## 1. Canvas contract

- Root: `<svg width="680" viewBox="0 0 680 H" xmlns="http://www.w3.org/2000/svg">`.
  The 680 width is load-bearing — all width math below assumes it. Never
  shrink the viewBox to hug narrow content; center the content instead.
- First element: the white background rect from the prelude, full canvas.
- Safe area: x = 40..640, y = 40..(H−40). Nothing may sit outside x = 0..680.
- H = (lowest y of any element, text baselines + 4px descent included) + 40.
  Compute it after layout — never guess.
- Negative x or y coordinates are forbidden; the viewBox starts at 0,0.

## 2. Pre-rasterize checklist (run over the finished SVG, every time)

1. Lowest element: max(y + height) over rects, max(baseline y + 4) over text.
   H equals that value + 40.
2. Rightmost element: max(x + width) over rects ≤ 680; for
   text-anchor="start" text, x + estimated width ≤ 680.
3. text-anchor="end" extends LEFT from x: estimated width must be ≤ x —
   risky whenever x < 60; prefer anchor "start" and right-shift the column.
4. No unintended overlaps: for every pair of elements not meant to layer
   (label/label, label/stroke, box/box), bounding boxes must not intersect.
   Deliberate overlaps only: a label centered in its own box, an arrowhead
   touching its target, a highlight rect behind its subject.
5. Same-row boxes: left box (x + width) + 20 ≤ right box x.
6. Every connector `<line>`/`<path>`/`<polyline>` carries `fill="none"`.
7. Every `marker-end` URL points at a marker that exists in `<defs>`.
8. Every `<text>` carries explicit font-family, font-size and fill.

## 3. Text metrics (estimate widths before drawing)

| Content | Rule of thumb |
|---|---|
| Latin at 14px | ~8 px per character |
| Latin at 12px | ~7 px per character |
| CJK at any size | ~1.1 × font-size per character (≈15 px at 14px) |
| Formulas, sub/superscripts, ∑ ∫ √ Å Γ | add 30-50% to the estimate |

- Box width = max(title estimate, subtitle estimate) + 24 (2 × 12px padding).
- Worked example: "Glucose (C₆H₁₂O₆)" is 18 chars at 14px ≈ 144px, plus the
  formula surcharge ≈ 190px, +24 → the rect must be ≥ 214px wide. A 160px box
  WILL overflow — shorten the label or widen the box.
- SVG text never auto-wraps: a second line is a second `<text>` element. If a
  subtitle needs wrapping it is too long — cut it to ≤5 words.

## 4. Tier packing (compute before placing)

- One horizontal row holds at most 4 full-width boxes (~140px each). For 5+
  items: shrink to ≤110px, wrap to a second row, or split the figure.
- Budget check for 4 boxes in the 40..640 safe span (600px):
  - WRONG: x = 40,160,260,360 at width 160 → adjacent boxes overlap 40-60px.
  - RIGHT: width 130, gap 20 → 4×130 + 3×20 = 580 ≤ 600; x = 50,200,350,500.
- Trees: size the leaf tier first; a parent spans at least its children.

## 5. Boxes and in-box text

- Heights: single-line box 44px, two-line box 56px. Same content type = same
  height across the figure.
- Corner radius rx=4 default, rx=8 max for emphasis; rx ≥ height/2 reads as a
  pill — deliberate use only.
- Stroke width 0.5 on all box borders. Colors per ramp trio: 50 fill /
  600 stroke / 800 title / 600 subtitle (palette table in style-contract.md).
- Vertical centering: give every in-box `<text>` the attribute
  `dominant-baseline="central"` with y at the CENTER of the slot it occupies.
  Two-line 56px box with top edge y0: title y = y0+18, subtitle y = y0+38.
- 24px inner padding; ≥12px between text and box edge; ≥60px edge-to-edge
  between boxes; sentence case; no emoji; subtitles ≤5 words.

## 6. Connectors and arrows

- Every connector carries `fill="none"` — paths default to black fill, and a
  curved connector without it renders as a filled black blob.
- Widths: 1 for arrows, 0.5 for leader lines and box borders. Arrowheads come
  from the prelude markers (`arrow-gray`, `arrow-purple`, `arrow-teal`) — use
  the one matching the line color; clone the marker in defs (new id, new
  stroke hex) for any other line color. Markers are fixed-color because the
  rasterizer does not support context-stroke.
- Arrowheads stop 10px before the target box edge.
- Intersection check before each connector: trace its segments against every
  box already placed; if it crosses any unrelated rect's interior, re-route
  with an L-bend: `<path d="M x1 y1 L x1 ym L x2 ym L x2 y2" fill="none"/>`.
- Arrow labels are a last resort — prefer the source/target box subtitle or
  the answer prose. A necessary label sits in clear space ≥8px from strokes.
- One flow direction per figure: all top-down or all left-right.
- Cycles are never drawn as rings: lay the stages in a line — stacked
  top-down when the labels exceed the horizontal row budget (§4) — and close
  the loop with a text note "↻ returns to <first stage>" near the last stage
  (if ↻ renders as tofu in the PNG, drop the glyph; the words alone suffice).
  Cyclic processes with per-stage detail become several figures delivered
  with connecting prose (contract narrative rules).

## 7. Labels outside boxes

- Minimize standalone labels: every text should live in a box or the legend.
- When margin labels are needed (typical in illustrative figures): pick ONE
  side — default right, text-anchor="start" — and reserve ≥140px of margin on
  that side. Connect with dashed leaders (stroke-dasharray="3 3", width 0.5,
  stroke #888780). Keep 8px clear air between any text and any stroke.
- Legend, when color encodes meaning: one row of 12×12 rx=2 swatch rects with
  12px labels, placed in the top or bottom margin clear of all shapes.

## 8. Routing: pick the figure type from the user's verb

| Request sounds like | Type | Rules |
|---|---|---|
| "walk me through", "what are the steps", "what's the flow / pipeline" | Flowchart | §9 |
| "what's the architecture", "what's inside", "how is it organized" | Structural | §10 |
| "how does X actually work", "explain X", "give me an intuition" | Illustrative | §11 |
| "database schema", "ERD", field lists | Not a diagram | markdown table in prose |

Same noun, different verb → different figure: "transformer architecture" is
structural; "how does attention work" is illustrative. The default for an
unqualified "how does X work" is illustrative — do not retreat to a flowchart
because it feels safer.

## 9. Flowchart specifics

- Max 4-5 nodes per figure. A request naming 6+ components becomes a stripped
  overview (boxes plus 1-2 main arrows, no fan-outs) plus one figure per
  interesting sub-flow, each with 3-4 nodes and room to breathe.
- Components: single-line node (44px), two-line node (56px, title + ≤5-word
  subtitle), gray trio for start/end/generic steps.
- Decision branches: put the condition in the TARGET box subtitle ("yes — …" /
  "no — …") instead of floating text on the lines.

## 10. Structural diagram specifics

- Containers: outermost rounded rect rx=20-24, ramp 50 fill + 600 stroke at
  0.5px, label INSIDE at top-left (x+20, baseline ≈ y+28; 14px weight 500,
  ramp 800).
- Inner regions: rx=8-12. Pick a RELATED ramp for related substructure and a
  CONTRASTING ramp for functionally different regions; parent and child never
  share the same fill (the hierarchy flattens visually).
- ≥20px padding inside every container; ≥16px gap between sibling regions;
  ≤3 nesting levels at 680px width.
- External inputs/outputs sit outside the outermost container, arrows
  pointing in/out, one-word or short labels.
- Regions contain text only: name (14px) + role (12px). No flowchart boxes,
  icons, or illustrations inside regions.
- Schematic containment beats literal shapes: a dashed rect
  (stroke-dasharray="4 3") labelled "Reactor vessel" reads cleaner than a
  drawn vessel outline that clips its content.

## 11. Illustrative diagram specifics

- Draw the mechanism, not boxes about it. Physical subjects get simplified
  cross-sections; abstract subjects get a spatial metaphor that makes the
  mechanism obvious (a stack of layers, a funnel into buckets, a ball on a
  surface). A good illustrative figure still works with the labels removed.
- Fidelity ceiling: every shape reads at a glance; a `<path>` needing more
  than ~6 segments is too detailed — simplify. Recognizable silhouette beats
  accurate contour.
- Color encodes intensity, not category: warm ramps (amber/coral/red) for
  heat/energy/active, cool (blue/teal) for cold/dormant, gray for inert
  structure. All hexes from the palette table.
- Shapes MAY layer deliberately (z-order = source order): a pipe entering a
  tank, lines fanning through layers. Text may NEVER be crossed by a stroke —
  labels go to the quiet margin with leaders (§7). No quiet region left means
  the drawing is too dense: remove something or split into two figures.
- Small state indicators are encouraged when they show physical state:
  triangles for flames, circles for particles/bubbles, wavy lines for
  steam/heat, short parallel lines for vibration. Simple primitives only.
- ONE `<linearGradient>` (exactly two stops, same ramp) is allowed per figure
  to show a continuous physical property (temperature stratification,
  pressure drop). No radial gradients, no multi-stop fades, no decoration.
  If two stacked flat rects say the same thing, use them instead.
- Lines stop at component edges: compute the boundary coordinate and end the
  segment there — never draw through a shape relying on a fill to hide it.

## 12. What does not exist on this channel

The Imagine guide assumes a live HTML/SVG renderer; here the SVG becomes a
static PNG. Therefore: no class / `<style>` / CSS variables, no
onclick/sendPrompt, no hover, no animation or @keyframes, no steppers or
tabs, no links, no dark mode. Express sequence with multiple figures and
prose; express state changes with before/after panels instead of toggles.
````

**§5 dominant-baseline 条目两版文案**（按 Task 2 Step 5 的 spike 结果选用，默认 A）：

- 文案 A（spike 第 7 项通过，居中正常）——上文已写入。
- 文案 B（spike 表明 dominant-baseline 不被支持时替换 §5 第 4 条）：

```
- Vertical centering: the rasterizer ignores dominant-baseline, so place
  baselines manually at y = slot center + 5. Two-line 56px box with top edge
  y0: title y = y0+23, subtitle y = y0+43. Single-line 44px box: y = y0+27.
```

- [ ] **Step 3: 验证 frontmatter 可解析、占位符在位**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import Skill
s = Skill(Path('matmaster/plugins/plotting/skills/plot-diagram'))
print(s.meta_info.name, '|', s.meta_info.mcp_server, '|', s.meta_info.depends_on)
"
grep -c 'PLUGIN_DIR\|SKILL_DIR' matmaster/plugins/plotting/skills/plot-diagram/SKILL.md
```

Expected: `plot-diagram | None | []`；grep 计数 ≥4（contract、discipline、prelude、svg2png 各一处以上）。

- [ ] **Step 4: Commit**

```bash
git add matmaster/plugins/plotting/skills/plot-diagram/
git commit -m "feat(plugins): add plot-diagram skill with svg discipline reference"
```

---

### Task 6: plot-chart skill

**Files:**
- Create: `matmaster/plugins/plotting/skills/plot-chart/SKILL.md`

- [ ] **Step 1: 写 SKILL.md（全文如下）**

````markdown
---
name: plot-chart
description: "Plot general data charts with matplotlib as answer figures: trends, convergence curves, spectra, scatter/correlation, bar comparisons, histograms, heatmaps. Use for numeric data with no materials convention — band/DOS/XRD/RDF/phase diagrams go to plot-materials; multi-panel reports to plot-report."
---

# Plot Chart

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Setup

Every chart script starts by applying the shared style (palette + CJK-capable
fonts + sizes + white background):

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
```

Save every figure with `mm_style.save_figure(fig, "/abs/workspace/name.png")`
— it applies the mandatory white-background savefig settings.

## Chart selection

| Data shape | Chart |
|---|---|
| y over an ordered x (time, step, parameter sweep) | line plot |
| few categories, one value each | vertical bar |
| many categories or long category names | horizontal bar, `figsize=mm_style.hbar_figsize(n)` |
| value distribution | histogram (`bins="auto"`); box plot to compare groups |
| two quantities, correlation question | scatter; add a fit line only if the fit was actually computed |
| matrix / pairwise values | `imshow` heatmap with a colorbar |
| share of a whole | one stacked horizontal bar — never a pie chart |

- Bars, areas and histogram patches use the trio: `facecolor=mm_style.fill(r)`,
  `edgecolor=mm_style.stroke(r)`, `linewidth=0.8`.
- Sort horizontal bars by value (largest on top) unless the category order
  itself carries meaning (time, sequence, a ranking the user gave).
- ≤4 series per panel — the full `CATEGORY_ORDER`, one ramp each; more →
  facet into small multiples or several figures.
- The default prop cycle pairs each `CATEGORY_ORDER` color with a distinct
  linestyle — keep both cues; never distinguish series by color alone.

## Axes, legend, grid

- Label every axis with quantity and unit: `ax.set_xlabel("Time (ps)")`.
- Legend: ≤2 series may sit inside a clear corner; otherwise place it outside
  right: `ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1))`. For
  comparison and convergence reads, carry the decisive value into the label:
  `label=f"PBE ({e_pbe:.3f} eV)"` — values computed, never invented.
- Grid and spines come from `mm_style.apply()` (subtle grid, no top/right
  spines) — do not restyle them per figure.
- Use a log scale when the data spans ≥2 decades (`ax.set_yscale("log")`) and
  say so in the axis label or caption.
- Number precision and minus-sign placement follow the contract.

## Scientific idioms

- Convergence curves (SCF energy, forces, k-mesh/cutoff sweeps): plot the
  convergence quantity — |ΔE| on semilog-y when it decays exponentially —
  draw the threshold as a gray dashed hline, and mark the accepted point with
  a marker plus its annotated value.
- Spectra and signals (IR/Raman/XAS, any intensity vs continuous variable):
  line without markers; annotate at most the ~5 principal peaks; y-axis
  "Intensity (arb. units)" when unnormalized.
- Parity plots (predicted vs reference): square aspect
  (`ax.set_aspect("equal")`), y=x gray dashed reference line, computed
  R²/MAE in a corner annotation.

## Deliver

AttachFigure the produced PNGs in one batch and reference each as
[[fig:<figure_id>]] with connecting prose (contract rules).
````

- [ ] **Step 2: 验证 frontmatter**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import Skill
s = Skill(Path('matmaster/plugins/plotting/skills/plot-chart'))
print(s.meta_info.name, '|', s.meta_info.mcp_server, '|', s.meta_info.depends_on)
"
```

Expected: `plot-chart | None | []`

- [ ] **Step 3: Commit**

```bash
git add matmaster/plugins/plotting/skills/plot-chart/
git commit -m "feat(plugins): add plot-chart skill"
```

---

### Task 7: plot-materials skill

**Files:**
- Create: `matmaster/plugins/plotting/skills/plot-materials/SKILL.md`

- [ ] **Step 1: 写 SKILL.md（全文如下）**

````markdown
---
name: plot-materials
description: "Plot standard materials-science figures with their domain conventions as answer figures: band structures, DOS/PDOS, XRD patterns, RDF g(r), MSD, and phase diagrams from computed results (pymatgen/ASE objects or parsed outputs). Generic numeric charts go to plot-chart."
---

# Plot Materials

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Setup

Same as plot-chart — apply the shared style before plotting:

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
```

`apply()` covers fonts (CJK), the white background, and axes you create
yourself. pymatgen plotters do NOT inherit it: they route through
`pretty_plot`, which re-imposes 30-48pt text, its own figure size, and a
Set1 palette after `apply()` ran. Every plotter-returned Axes therefore goes
through `mm_style.restyle(ax)` and a recolor pass before saving.

## Plotter-first policy

Prefer pymatgen's plotters over hand-drawn axes — they already implement the
domain conventions (k-path labels, Fermi alignment, hull construction):

| Figure | Plotter |
|---|---|
| band structure | `pymatgen.electronic_structure.plotter.BSPlotter(bs).get_plot()` |
| DOS / PDOS | `pymatgen.electronic_structure.plotter.DosPlotter()` — `add_dos()` then `get_plot()` |
| XRD pattern | `pymatgen.analysis.diffraction.xrd.XRDCalculator().get_plot(structure)` |
| phase diagram | `pymatgen.analysis.phase_diagram.PDPlotter(pd, backend="matplotlib").get_plot()` |

- `get_plot` returns a matplotlib Axes; finish with `mm_style.restyle(ax)`,
  a recolor pass, then `mm_style.save_figure(ax.figure, path)`.
- The sandbox has no plotly — always pass `backend="matplotlib"` to PDPlotter.
- Recolor plotter output to palette colors (`mm_style.stroke("purple")`, …):
  pymatgen's own defaults (Set1 palette, black hull and stems) clash with the
  contract.
- Hand-draw with plot-chart techniques only when no plotter covers the figure
  (RDF, MSD, custom parsed outputs).

## Domain conventions per figure

Plotter defaults are marked (default); everything else is your restyle work.

- Band structure: high-symmetry path labels Γ, X, … (default); spin channels
  by linestyle — solid up, dashed down (default); y axis "E − E_F (eV)";
  the Fermi line arrives colored dash-dot — restyle it gray dashed at 0; set
  the window to [−4, +4] eV via `ax.set_ylim` unless asked otherwise; name
  spin channels in the legend yourself when spin-polarized.
- DOS / PDOS: energy lands on the x axis with the Fermi line black dashed at
  0 and spin-down mirrored negative (defaults); gray the Fermi line; y axis
  "DOS (states/eV)"; PDOS overlaid per element/orbital with a legend. Paired
  beside a band structure, pass `get_plot(invert_axes=True)` so energy goes
  on the shared y axis.
- XRD: x axis 2θ in degrees, vertical stems, intensities normalized to 100
  (defaults); the default annotates every reflection — pass
  `annotate_peaks=False` and label only the ~5 strongest yourself; recolor
  the black stems to a palette stroke; state the wavelength in the caption
  (Cu Kα unless you chose otherwise).
- RDF: x "r (Å)", y "g(r)"; gray hline at g = 1; annotate the first-shell
  peak position from the data. (Hand-drawn — plot-chart techniques.)
- MSD: x "t (ps)", y "MSD (Å²)"; when a diffusion coefficient was computed,
  draw the fitted line and put D in the legend. (Hand-drawn.)
- Phase diagram: stable entries arrive labelled with reduced formulas
  (default); the hull arrives black with Set1 markers — restyle hull lines
  gray and markers to a palette stroke; put e_above_hull values in the
  caption when conclusions depend on them.
- k-mesh / cutoff convergence: use the convergence idiom from plot-chart.

## Data honesty

Plot only arrays parsed from real outputs in the workspace or values the user
gave. Never synthesize a "typical" band structure, spectrum, or hull — if the
computation is missing, say what must be run instead (contract rule).

## Deliver

AttachFigure the produced PNGs in one batch and reference each as
[[fig:<figure_id>]] with connecting prose (contract rules).
````

- [ ] **Step 2: 验证 frontmatter**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import Skill
s = Skill(Path('matmaster/plugins/plotting/skills/plot-materials'))
print(s.meta_info.name, '|', s.meta_info.mcp_server, '|', s.meta_info.depends_on)
"
```

Expected: `plot-materials | None | []`

- [ ] **Step 3: Commit**

```bash
git add matmaster/plugins/plotting/skills/plot-materials/
git commit -m "feat(plugins): add plot-materials skill"
```

---

### Task 8: plot-report skill

**Files:**
- Create: `matmaster/plugins/plotting/skills/plot-report/SKILL.md`

- [ ] **Step 1: 写 SKILL.md（全文如下）**

````markdown
---
name: plot-report
description: "Compose a multi-panel summary figure with matplotlib subplot_mosaic: headline metric cards on top, aligned supporting charts below, (a)(b)(c) panel labels. Use for a combined overview, summary board, or publication-style multi-panel figure; single charts go to plot-chart or plot-materials."
---

# Plot Report

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Compose or separate?

Compose one multi-panel figure when the panels answer a single question at a
glance — final summary, side-by-side comparison, paper-style figure. Keep
separate figures with prose between when the answer walks through steps
(contract narrative rules). ≤6 panels per figure; more → split. Faceted
small multiples of one chart type (same axes, split by a condition) are
plot-chart's facet case, not a report figure.

## Layout recipe

Apply the shared style first, then build the mosaic:

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()

fig, axs = plt.subplot_mosaic(
    [["m0", "m1", "m2"], ["main", "main", "side"]],
    figsize=(10, 6),
    height_ratios=[1, 3],
    layout="constrained",
)
```

- The mosaic spec mirrors the visual layout: metric cards across the top row,
  the main chart spanning columns below, side panels beside it. Adapt names,
  spans and `height_ratios` to the content; keep cards on top.
- Metric cards — computed headline numbers only, borderless. Cards show
  independent quantities, not a categorical grouping, so each card may take
  its own ramp without counting against the ≤2-ramp budget; reuse a chart
  panel's ramp when the card shows the same quantity:

```python
def metric_card(ax, value, label, ramp="purple"):
    ax.axis("off")
    ax.text(0.5, 0.58, value, ha="center", va="center", fontsize=22,
            fontweight="medium", color=mm_style.title_color(ramp))
    ax.text(0.5, 0.22, label, ha="center", va="center", fontsize=12,
            color=mm_style.stop("gray", 600))
```

- Panel labels on every chart panel (skip the cards), same corner everywhere:

```python
ax.text(0.02, 0.98, "(a)", transform=ax.transAxes, fontsize=14,
        fontweight="medium", va="top")
```

- Title hierarchy: the figure-level message lives only in `fig.suptitle(...)`
  (14, medium — mm_style default); panel titles use
  `ax.set_title(..., fontsize=12)`; axis labels stay at 14.
- Alignment: share axes (`sharex`/`sharey`) wherever panels show the same
  quantity; the constrained layout engine aligns labels automatically.
- One color mapping across all panels: the same series or quantity keeps the
  same ramp everywhere in the figure.

## Deliver

Save with `mm_style.save_figure(fig, path)` and attach. The caption is one
self-contained sentence summarizing the whole figure (contract rule); walk
the panels — "(a) …, (b) …, (c) …" — in the answer body prose right after
the [[fig:<figure_id>]] reference.
````

- [ ] **Step 2: 验证 frontmatter**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import Skill
s = Skill(Path('matmaster/plugins/plotting/skills/plot-report'))
print(s.meta_info.name, '|', s.meta_info.mcp_server, '|', s.meta_info.depends_on)
"
```

Expected: `plot-report | None | []`

- [ ] **Step 3: Commit**

```bash
git add matmaster/plugins/plotting/skills/plot-report/
git commit -m "feat(plugins): add plot-report skill"
```

---

### Task 9: 收尾验证

**Files:** 无新建；只跑验证命令，发现问题就地修复后补提交。

- [ ] **Step 1: 注册中心冒烟——plugin 归组 + 4 skill 全部发现**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import SkillRegistry
r = SkillRegistry(Path('matmaster/plugins'))
ctx = r.get_meta_info_context()
print('\n'.join(line for line in ctx.splitlines() if 'plot' in line or 'plotting' in line))
"
```

Expected: 一行 `[Plugin: plotting] …` 与四行缩进的 `[Skill: plot-diagram/chart/materials/report] …`。

- [ ] **Step 2: SkillTool 占位符替换冒烟**

```bash
.venv/bin/python <<'EOF'
from pathlib import Path

from matmaster.skills.registry import SkillRegistry

r = SkillRegistry(Path('matmaster/plugins'))
s = r.get_skill('plot-chart')
body = s.get_full_info().replace('${PLUGIN_DIR}', str(s.plugin_dir))
assert '${PLUGIN_DIR}' not in body and 'plugins/plotting/shared' in body
print('placeholder substitution ok')
EOF
```

Expected: `placeholder substitution ok`

- [ ] **Step 3: 四个 SKILL.md 第一节强制措辞逐字一致**

```bash
grep -c 'Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing' matmaster/plugins/plotting/skills/*/SKILL.md
```

Expected: 四个文件各计数 1。

- [ ] **Step 4: pre-commit 全量过一遍新文件**

```bash
git diff --name-only codex/provider-stage1..HEAD | xargs pre-commit run --files
```

Expected: 全部 Passed/Skipped。若 black/isort/end-of-file-fixer 自动改了文件，`git add -u && git commit -m "chore(plugins): satisfy pre-commit hooks"`。若 `pre-commit` 不在 PATH，可跳过本步——每次 `git commit` 时钩子已经跑过同样的检查。

- [ ] **Step 5: 对照 spec 验收口径收尾**

spec §9 的四条人工端到端冒烟（触发命中 → 读契约 → 沙箱产图 → AttachFigure → `[[fig:id]]`）依赖重建后的镜像与真实会话，属合并后人工验收，不在本计划内执行。向用户报告：分支就绪、spike 结论、镜像需用新 Dockerfile.remote 重建后方可做 E2E 验收。
