"""Phase D: builtin_skills_sync 双轨拆分测试。

覆盖：
- 扁平轨 `_scan_builtin_skills` 不再压平 plugin 成员（无 tags=[plugin.name]）；
- plugin 轨 `_scan_builtin_plugins` 整包产出（含成员 dir、保留顶层目录的 zip）；
- D18：plugin 名恒取目录名；空 plugin 跳过；`_` 前缀跳过。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import src.services.builtin_skills_sync as sync


def _write_skill(skill_dir: Path, name: str, description: str = "desc") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )


def _write_plugin_manifest(
    plugin_dir: Path, *, category: str | None = None, description: str = ""
) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    if category is not None:
        lines.append(f"category: {category}")
    if description:
        lines.append(f"description: {description}")
    (plugin_dir / "plugin.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scan_skills_excludes_plugin_members(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    plugins_root = tmp_path / "plugins"
    _write_skill(skills_root / "flat-one", "flat-one")
    _write_skill(skills_root / "_hidden", "hidden-skill")
    _write_plugin_manifest(plugins_root / "plotting", category="viz", description="P")
    _write_skill(plugins_root / "plotting" / "skills" / "plot", "plot-chart")

    monkeypatch.setattr(sync, "_SKILLS_ROOT", skills_root)
    monkeypatch.setattr(sync, "_PLUGINS_ROOT", plugins_root)

    skills = sync._scan_builtin_skills({})
    names = {s["name"] for s in skills}

    assert names == {"flat-one"}
    for s in skills:
        assert s.get("tags") is None


def test_scan_plugins_whole_package(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    _write_plugin_manifest(
        plugins_root / "plotting", category="viz", description="Plotting tools"
    )
    _write_skill(plugins_root / "plotting" / "skills" / "plot", "plot-chart", "draw")
    (plugins_root / "plotting" / "shared").mkdir(parents=True)
    (plugins_root / "plotting" / "shared" / "util.py").write_text("x=1\n", "utf-8")

    monkeypatch.setattr(sync, "_PLUGINS_ROOT", plugins_root)

    plugins = sync._scan_builtin_plugins()
    assert len(plugins) == 1
    p = plugins[0]
    assert p["name"] == "plotting"
    assert p["category"] == "viz"
    assert p["description"] == "Plotting tools"

    members = p["member_skills"]
    assert members == [
        {"name": "plot-chart", "description": "draw", "dir": "skills/plot"}
    ]

    names = zipfile.ZipFile(io.BytesIO(p["zip_bytes"])).namelist()
    assert "plotting/plugin.yaml" in names
    assert "plotting/skills/plot/SKILL.md" in names
    assert "plotting/shared/util.py" in names
    assert all(n.startswith("plotting/") for n in names)


def test_scan_plugins_skips_empty(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    _write_plugin_manifest(plugins_root / "empty", category="x")

    monkeypatch.setattr(sync, "_PLUGINS_ROOT", plugins_root)

    assert sync._scan_builtin_plugins() == []


def test_scan_plugins_skips_underscore_member(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    _write_plugin_manifest(plugins_root / "p", category="x")
    _write_skill(plugins_root / "p" / "skills" / "real", "real")
    _write_skill(plugins_root / "p" / "_internal" / "hidden", "hidden")

    monkeypatch.setattr(sync, "_PLUGINS_ROOT", plugins_root)

    plugins = sync._scan_builtin_plugins()
    assert len(plugins) == 1
    assert [m["name"] for m in plugins[0]["member_skills"]] == ["real"]


def test_zip_plugin_dir_is_deterministic(tmp_path):
    plugin_dir = tmp_path / "demo"
    _write_plugin_manifest(plugin_dir, category="x", description="d")
    _write_skill(plugin_dir / "skills" / "a", "a")

    first = sync._zip_plugin_dir(plugin_dir)
    second = sync._zip_plugin_dir(plugin_dir)
    assert first[1] == second[1]


def test_builtin_plugins_name_equals_dir(tmp_path, monkeypatch):
    """D18 锚点：真实内置 plugin 的 name 恒等于其顶层目录名。"""
    real_plugins_root = (
        Path(sync.__file__).resolve().parents[2] / "matmaster" / "plugins"
    )
    if not real_plugins_root.exists():
        return
    monkeypatch.setattr(sync, "_PLUGINS_ROOT", real_plugins_root)
    for p in sync._scan_builtin_plugins():
        assert p["name"]
        assert (real_plugins_root / p["name"] / "plugin.yaml").exists()
