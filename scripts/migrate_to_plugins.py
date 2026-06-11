"""一次性迁移：skill 双轨化（spec 2026-06-10 §3.3/§4/§6.1）。跑完即弃。

用法：python scripts/migrate_to_plugins.py
失败恢复：git checkout -- matmaster/ && git clean -fd matmaster/
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "matmaster" / "skills"
PLUGINS = REPO / "matmaster" / "plugins"

# --- §4 剪枝：10 个 skill + 3 个仅被它们引用/空壳的非 skill 目录 ---
DELETE_DIRS = [
    "compliance-guardian",
    "deep-survey",
    "lit-data-organizer",
    "manuscript-scribe",
    "md-analysis",
    "poly-forcefield",
    "poly-generator",
    "result-analysis",
    "tasker-polar-surface",
    "vaspkit-postprocess",
    "_common",  # 仅被上列待删 skill 引用
    "bohrium-job",  # 空壳：scripts/ 下仅 __pycache__
    "polyFF",  # 仅被 poly-forcefield 引用
]

# --- §3.3 plugin 清单：plugin 名 → (category, {成员目录名: 相对 SKILLS 的现路径}) ---
PLUGINS_SPEC: dict[str, tuple[str, dict[str, str]]] = {
    "atomic-structure-ops": (
        "structure-modeling",
        {
            "atomic-structure": "atomic-structure",
            "inspect-atomic-structure": "inspect-atomic-structure",
            "build-crystal-from-params": "build-crystal-from-params",
            "transform-atomic-structure": "transform-atomic-structure",
            "assemble-atomic-structure": "assemble-atomic-structure",
            "operate-molecular-crystal": "operate-molecular-crystal",
            "sample-atomic-structures": "sample-atomic-structures",
        },
    ),
    "structure-search": (
        "structure-modeling",
        {
            "mcp-mat-struct-db": "lazymcp/mcp-mat-struct-db",
            "retrieve-structure": "retrieve-structure",
        },
    ),
    "abacus": ("simulation", {"abacus": "abacus", "pyatb": "pyatb"}),
    "mlips": ("simulation", {"mlips": "mlips", "aissq-explorer": "aissq-explorer"}),
    "data-mining": (
        "analysis",
        {
            "mcp-mat-compdart": "lazymcp/mcp-mat-compdart",
            "composition-optimization": "playground-skills/composition-optimization",
        },
    ),
    "task-planning": (
        "workflow-system",
        {
            "plan-writer": "planner/plan-writer",
            "plan-checker": "planner/plan-checker",
            "plan-executor": "plan-executor",
            "spec-writer": "planner/spec-writer",
            "acceptance-writer": "planner/acceptance-writer",
            "stack-checker": "planner/stack-checker",
        },
    ),
    "vasp": ("simulation", {"vasp": "vasp"}),
    "cp2k": ("simulation", {"cp2k": "cp2k"}),
    "quantum-espresso": ("simulation", {"quantum_espresso": "quantum_espresso"}),
    "abinit": ("simulation", {"abinit": "abinit"}),
    "pyscf": ("simulation", {"pyscf": "pyscf"}),
    "orca": ("simulation", {"orca": "orca"}),
    "lammps": ("simulation", {"lammps": "lammps"}),
    "gromacs": ("simulation", {"gromacs": "gromacs"}),
    "gpumd": ("simulation", {"gpumd": "gpumd"}),
}

# --- 幸存者上移扁平根：相对 SKILLS 的现路径 → 顶层目录名 ---
MOVE_TO_FLAT = {
    "lazymcp/mcp-mat-doc": "mcp-mat-doc",
    "lazymcp/mcp-mat-xrd": "mcp-mat-xrd",
    "lazymcp/mcp-mat-nmr": "mcp-mat-nmr",
    "lazymcp/mcp-mat-electron-microscope": "mcp-mat-electron-microscope",
    "playground-skills/pxrd-refinement": "pxrd-refinement",
    "playground-skills/checkcif-validator": "checkcif-validator",
}

LEGACY_SUBDIRS = ["lazymcp", "planner", "playground-skills"]

DELETED_SKILL_NAMES = DELETE_DIRS[:10]

BUILTIN_TAGS_REDUCED = """\
categories:
  analysis:
    groups:
      general-data-analysis:
        skills:
          - data-analysis
      characterization:
        skills:
          - pxrd-refinement
          - checkcif-validator
          - mcp-mat-xrd
          - mcp-mat-nmr
          - mcp-mat-electron-microscope

  research-writing:
    groups:
      literature:
        skills:
          - mcp-mat-doc
      academic-writing:
        skills:
          - proposal-review

  workflow-system:
    groups:
      system-tools:
        skills:
          - skill-manager
          - image-manager
          - session-analyzer
"""

# --- skill 资产内旧引用定点修复（路径为迁移后的新位置）---
_GROMACS_OLD = (
    "## Post-Processing\n"
    "\n"
    "After job completion, use the **md-analysis** skill for trajectory "
    "analysis (RMSD, RMSF, RDF, MSD, H-bonds, energy).\n"
)
_TASKER_OLD = (
    "   Use `../playground-skills/tasker-polar-surface/SKILL.md`. If the material\n"
    "   and Miller index are not in local lookup data, search literature before\n"
    "   finalizing the provisional Tasker type. Always validate the actual slab after\n"
    "   construction.\n"
)
_TASKER_NEW = (
    "   Handle the polar analysis inline: classify the Tasker type from the\n"
    "   stacking sequence, prefer nonpolar or symmetric terminations, and search\n"
    "   literature when the material and Miller index are unfamiliar. Always\n"
    "   validate the actual slab after construction.\n"
)
_COMPOPT_STEP2_OLD = (
    "   - If not provided (or if literature search is planned regardless):\n"
    "     - Call `deep-survey` to collect evidence. Depth choice: `--depth brief`"
    " for seed-only sub-step (3-5 calls, no report); `--depth standard` for"
    " concise survey file + evidence (6-8 calls); `--depth deep` only when user"
    " explicitly wants a comprehensive review.\n"
    "     - `deep-survey` always produces `collected_<topic>.json`. Pass it to"
    " `lit-data-organizer` (build_lit_table.py) to build the canonical evidence"
    " table before sampling seeds.\n"
)
_COMPOPT_STEP2_NEW = (
    "   - If not provided (or if literature search is planned regardless),\n"
    "     collect literature evidence with `mat_doc_*` / `mat_sn_*` tools and\n"
    "     record candidate compositions with sources before sampling seeds.\n"
)
_COMPOPT_TABLE_OLD = (
    "| Initial data: No, Surrogate: Yes | deep-survey -> lit-data-organizer ->"
    " seeds -> composition->structure if needed -> run DART GA |\n"
    "| Initial data: No, Surrogate: No | deep-survey -> lit-data-organizer ->"
    " seeds -> composition->structure if needed -> screening/fallback |\n"
)
_COMPOPT_TABLE_NEW = (
    "| Initial data: No, Surrogate: Yes | literature search -> seeds ->"
    " composition->structure if needed -> run DART GA |\n"
    "| Initial data: No, Surrogate: No | literature search -> seeds ->"
    " composition->structure if needed -> screening/fallback |\n"
)
_COMPOPT_SCRIBE_OLD = (
    "   - If using `manuscript-scribe` to produce the survey report, use"
    " `--profile literature_review` (matches deep-survey's 5-section output"
    " structure exactly).\n"
)
_COMPOPT_RULE_OLD = (
    "**Evidence persistence rule (cross-cutting)**: If ANY literature retrieval"
    " is performed during this workflow — including deep-survey or direct"
    ' `mat_sn_*` calls on the "Initial data: Yes" paths — the retrieved evidence'
    " MUST be passed through `lit-data-organizer` (build_lit_table.py) before"
    " proceeding. deep-survey always produces `collected_<topic>.json`; pass it"
    " as `--input_json` to `build_lit_table.py`. Whether the canonical table is"
    " consumed downstream (seed augmentation, Pareto analysis, or simply as an"
    " artifact) is the executor's decision. The goal is: no evidence is silently"
    " discarded.\n"
)
_COMPOPT_RULE_NEW = (
    "**Evidence persistence rule (cross-cutting)**: If ANY literature retrieval"
    " is performed during this workflow — including direct `mat_sn_*` calls on"
    ' the "Initial data: Yes" paths — record the retrieved evidence with'
    " sources in a structured table before proceeding. Whether the table is"
    " consumed downstream (seed augmentation, Pareto analysis, or simply as an"
    " artifact) is the executor's decision. The goal is: no evidence is"
    " silently discarded.\n"
)
_COMPOPT_DEPTH_OLD = (
    "For the depth choice when calling deep-survey: use `--depth brief` when"
    " only seed data is needed; use `--depth standard` when a concise survey"
    " file is also wanted; use `--depth deep` only when the user explicitly"
    " requests a comprehensive review report.\n"
)
_COMPOPT_DEPTH_NEW = (
    "For the depth of literature search: keep it light when only seed data is"
    " needed; broaden it when a concise survey file is also wanted; go"
    " comprehensive only when the user explicitly requests a review report.\n"
)
_COMPOPT_DIRECT_OLD = (
    "- For direct mode, avoid heavy deep-survey unless user explicitly asks"
    " for report/file output.\n"
)
_COMPOPT_DIRECT_NEW = (
    "- For direct mode, avoid heavy literature surveys unless user explicitly"
    " asks for report/file output.\n"
)

CONTENT_FIXES: list[tuple[str, str, str]] = [
    ("plugins/gromacs/skills/gromacs/SKILL.md", _GROMACS_OLD, ""),
    (
        "plugins/atomic-structure-ops/skills/atomic-structure/SKILL.md",
        _TASKER_OLD,
        _TASKER_NEW,
    ),
    (
        "plugins/atomic-structure-ops/skills/inspect-atomic-structure/SKILL.md",
        "matmaster/skills/playground-skills/retrieve-structure/scripts/assess_structure.py",
        "matmaster/plugins/structure-search/skills/retrieve-structure/scripts/assess_structure.py",
    ),
    (
        "plugins/abacus/skills/abacus/references/output_params.md",
        " For scripted extraction and plots after a run, see"
        " `matmaster/skills/playground-skills/result-analysis`"
        " (`parse_abacus.py`, `plot_publication.py`).",
        "",
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_STEP2_OLD,
        _COMPOPT_STEP2_NEW,
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_TABLE_OLD,
        _COMPOPT_TABLE_NEW,
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_SCRIBE_OLD,
        "",
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_RULE_OLD,
        _COMPOPT_RULE_NEW,
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_DEPTH_OLD,
        _COMPOPT_DEPTH_NEW,
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_DIRECT_OLD,
        _COMPOPT_DIRECT_NEW,
    ),
]


def _move(src: Path, dst: Path) -> None:
    assert src.is_dir(), f"missing source: {src}"
    assert not dst.exists(), f"destination exists: {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _strip_skill_type(md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    match = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", text, re.DOTALL)
    if not match:
        return
    kept = [
        line
        for line in match.group(2).split("\n")
        if not line.strip().startswith("skill_type:")
    ]
    new_text = text[: match.start(2)] + "\n".join(kept) + text[match.end(2) :]
    if new_text != text:
        md_path.write_text(new_text, encoding="utf-8")


def _apply_content_fixes() -> None:
    for rel, old, new in CONTENT_FIXES:
        path = REPO / "matmaster" / rel
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        assert count == 1, f"{path}: expected 1 occurrence, found {count}:\n{old!r}"
        path.write_text(text.replace(old, new).rstrip() + "\n", encoding="utf-8")


def _purge_caches(root: Path) -> None:
    for junk in root.rglob("__pycache__"):
        shutil.rmtree(junk)
    for junk in root.rglob(".DS_Store"):
        junk.unlink()


def _verify() -> None:
    flat = sorted(p.parent.name for p in SKILLS.rglob("SKILL.md"))
    assert len(flat) == 11, f"flat skills != 11: {flat}"
    manifests = sorted(PLUGINS.rglob("plugin.yaml"))
    assert len(manifests) == 15, f"plugin.yaml != 15: {manifests}"
    members = sorted(p.parent.name for p in PLUGINS.rglob("SKILL.md"))
    assert len(members) == 30, f"plugin members != 30: {members}"
    for legacy in LEGACY_SUBDIRS:
        assert not (SKILLS / legacy).exists(), f"legacy dir survives: {legacy}"
    pool = "\n".join(
        p.read_text(encoding="utf-8")
        for root in (SKILLS, PLUGINS)
        for p in root.rglob("*.md")
    )
    for name in DELETED_SKILL_NAMES:
        assert name not in pool, f"stale reference to deleted skill: {name}"
    assert "skill_type:" not in pool, "skill_type frontmatter survives"
    print(f"OK: flat={len(flat)} plugins={len(manifests)} members={len(members)}")


def main() -> None:
    assert SKILLS.is_dir(), SKILLS
    assert not PLUGINS.exists(), f"{PLUGINS} already exists"

    # 1. 剪枝
    for rel in DELETE_DIRS:
        target = SKILLS / "playground-skills" / rel
        assert target.is_dir(), f"missing delete target: {target}"
        shutil.rmtree(target)

    # 2. 入轨：30 个 skill 进 plugins/<plugin>/skills/<skill>/，并写瘦清单
    for plugin_name, (category, member_map) in PLUGINS_SPEC.items():
        plugin_dir = PLUGINS / plugin_name
        for member_dir_name, src_rel in member_map.items():
            _move(SKILLS / src_rel, plugin_dir / "skills" / member_dir_name)
        members = ", ".join(member_map)
        manifest = (
            f"name: {plugin_name}\n"
            f"category: {category}\n"
            f'description: "{plugin_name} plugin（成员: {members}；'
            f'描述占位，待人工补全）"\n'
        )
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    # 3. 幸存者上移扁平根
    for src_rel, dst_name in MOVE_TO_FLAT.items():
        _move(SKILLS / src_rel, SKILLS / dst_name)

    # 4. 解散三个物理子目录（清掉缓存垃圾后必须为空）
    for legacy in LEGACY_SUBDIRS:
        legacy_dir = SKILLS / legacy
        _purge_caches(legacy_dir)
        leftovers = sorted(p.name for p in legacy_dir.iterdir())
        assert not leftovers, f"{legacy_dir} not empty: {leftovers}"
        legacy_dir.rmdir()

    # 5. builtin_tags 缩减为扁平轨标签目录
    (SKILLS / "builtin_tags.yaml").write_text(BUILTIN_TAGS_REDUCED, encoding="utf-8")

    # 6. 全部存留 SKILL.md 剥除 skill_type
    for root in (SKILLS, PLUGINS):
        for md_path in root.rglob("SKILL.md"):
            _strip_skill_type(md_path)

    # 7. skill 资产内旧引用定点修复
    _apply_content_fixes()

    _verify()


if __name__ == "__main__":
    main()
