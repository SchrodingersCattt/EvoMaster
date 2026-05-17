from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_core_compaction_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/core" / ("context" + "_compactor.py")

    assert not shim_path.exists()


def test_types_context_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/types" / "context.py"

    assert not shim_path.exists()


def test_types_current_input_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/types" / ("current" + "_input.py")

    assert not shim_path.exists()


def test_manifests_package_is_removed() -> None:
    package_path = ROOT / "matmaster" / "manifests"
    tests_path = ROOT / "tests/matmaster" / "manifests"

    assert not package_path.exists()
    assert not tests_path.exists()


def test_model_history_restore_service_shim_is_removed() -> None:
    shim_path = ROOT / "src/services" / ("history" + "_restore_service.py")

    assert not shim_path.exists()


def test_agent_run_instructions_helper_is_removed() -> None:
    helper_path = ROOT / "src/services" / ("agent_run" + "_instructions.py")

    assert not helper_path.exists()


def test_context_scanner_does_not_decode_raw_rows() -> None:
    scanner_path = ROOT / "matmaster" / "context" / "scanner.py"
    text = scanner_path.read_text(encoding="utf-8")

    forbidden = [
        "coerce" + "_session_events",
        "coerce" + "_event_id",
        "_freeze_json_value",
        "_coerce_content",
        "_coerce_optional_str",
        "Mapping[str, Any]",
    ]
    for token in forbidden:
        assert token not in text


def test_core_context_does_not_import_service_codec() -> None:
    context_root = ROOT / "matmaster" / "context"
    offenders = []
    for path in context_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "src.services.session_event_codec" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_matmaster_context_does_not_reference_skill_registry() -> None:
    context_root = ROOT / "matmaster" / "context"
    offenders: list[str] = []
    for path in context_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "skill_registry" in text or "SkillRegistry" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    offenders = [path for path in offenders if "system_prompt.py" not in path]
    assert offenders == [], (
        "matmaster/context/* must not depend on SkillRegistry; "
        f"violations: {offenders}"
    )


def test_session_skills_source_does_not_export_legacy_helpers() -> None:
    from matmaster.context.sources import skills

    assert not hasattr(skills, "resolve_active_skills")
    assert not hasattr(skills, "skill_name")
    assert not hasattr(skills.SessionSkillsSource, "from_events")
