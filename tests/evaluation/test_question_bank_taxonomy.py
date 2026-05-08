from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evaluation.core.question_tags import QuestionTag
from evaluation.core.schemas import QuestionBank
from tests.evaluation.capability_abbrev import CAPABILITY_TO_TWO_LETTER

BUSINESS_LINE_DOMAINS = [
    "battery",
    "catalysis",
    "polymer",
    "alloy",
    "semiconductor",
]

# All values accepted by DomainLiteral (schemas.py), including non-business-line bucket.
VALID_ALL_DOMAINS = BUSINESS_LINE_DOMAINS + ["agnostic"]

REMOVED_LEGACY_DOMAINS = [
    "struct",
    "elec",
    "mech",
    "thermo",
    "kinetic",
    "general",
    "incar",
    "scxrd",
    "mlip",
    "domain_agnostic",
]


def _bank_yaml_filename_matches_capability_domain(
    *, capability: str, domain: str, filename: str
) -> bool:
    """Match bank basename ``{xx}_{domain}.yaml`` or split ``{xx}_{domain}_{tag}.yaml``.

    ``xx`` = two-letter capability code; ``tag`` splits one (capability, domain) across
    files (e.g. ``vasp`` / ``abacus``). ``tag`` is one segment (``.+``), may contain ``_``.
    """
    try:
        xx = CAPABILITY_TO_TWO_LETTER[capability]
    except KeyError:
        return False
    if not filename.endswith(".yaml"):
        return False
    stem = filename[: -len(".yaml")]
    pat = rf"^{re.escape(xx)}_{re.escape(domain)}(?:_(?P<tag>.+))?$"
    return re.fullmatch(pat, stem) is not None


def _minimal_question(
    *, capability: str, domain: str, tags: list[str] | None = None
) -> dict:
    return {
        "id": f"{capability}_{domain}",
        "capability": capability,
        "domain": domain,
        "intent": "taxonomy test",
        "human_prompt_seed": "x",
        "tags": tags or [],
        "reference_answers": [{"key": "unused", "value": "x"}],
        "scoring_checklist": [
            {
                "id": "unused",
                "criterion": "unused",
                "axis": "correctness",
                "verify": "llm_binary_judge",
            }
        ],
    }


def test_capability_two_letter_codes_are_unique() -> None:
    vals = list(CAPABILITY_TO_TWO_LETTER.values())
    assert len(vals) == len(set(vals))


def test_question_bank_rejects_mismatched_top_level_capability_hint() -> None:
    with pytest.raises(ValidationError, match="top-level capability"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "questions": [
                    _minimal_question(
                        capability="workflow_orchestration", domain="battery"
                    )
                ],
            }
        )


def test_question_bank_requires_top_level_capability() -> None:
    with pytest.raises(ValidationError, match="top-level capability is required"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "domain": "battery",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis", domain="battery"
                    )
                ],
            }
        )


def test_question_bank_rejects_mismatched_top_level_domain_hint() -> None:
    with pytest.raises(ValidationError, match="top-level domain"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "domain": "catalysis",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis", domain="battery"
                    )
                ],
            }
        )


def test_question_bank_requires_top_level_domain() -> None:
    with pytest.raises(ValidationError, match="top-level domain is required"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis", domain="battery"
                    )
                ],
            }
        )


def test_canonical_tags_disjoint_from_capability_domain_literals() -> None:
    """Canonical tag strings use prefixes; they must not equal capability/domain literals."""
    caps = {
        "structure_construction",
        "structure_retrieval",
        "scientific_analysis",
        "workflow_orchestration",
        "execution_contract",
        "data_diagnosis",
        "batch_processing",
        "safety_refusal",
        "input_generation",
    }
    doms = {"battery", "catalysis", "polymer", "alloy", "semiconductor", "agnostic"}
    tag_values = {m.value for m in QuestionTag}
    assert tag_values.isdisjoint(caps)
    assert tag_values.isdisjoint(doms)


def test_question_bank_rejects_generic_process_tag() -> None:
    with pytest.raises(ValidationError, match="generic process tag"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "domain": "battery",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis",
                        domain="battery",
                        tags=["workflow"],
                    )
                ],
            }
        )


def test_question_bank_rejects_noncanonical_tag_alias() -> None:
    with pytest.raises(ValidationError, match="use canonical tag 'srtio3'"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "domain": "battery",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis",
                        domain="battery",
                        tags=["SrTiO3"],
                    )
                ],
            }
        )


def test_question_bank_rejects_tag_with_noncanonical_case() -> None:
    with pytest.raises(ValidationError, match="use canonical tag 'hea'"):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "domain": "battery",
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis",
                        domain="battery",
                        tags=["HEA"],
                    )
                ],
            }
        )


@pytest.mark.parametrize("domain", VALID_ALL_DOMAINS)
def test_question_bank_accepts_all_literal_domains(domain: str) -> None:
    bank = QuestionBank.model_validate(
        {
            "version": "v5",
            "capability": "scientific_analysis",
            "domain": domain,
            "questions": [
                _minimal_question(capability="scientific_analysis", domain=domain)
            ],
        }
    )
    assert bank.domain == domain


@pytest.mark.parametrize("domain", REMOVED_LEGACY_DOMAINS)
def test_question_bank_rejects_removed_legacy_domains(domain: str) -> None:
    with pytest.raises(ValidationError, match=domain):
        QuestionBank.model_validate(
            {
                "version": "v5",
                "capability": "scientific_analysis",
                "domain": domain,
                "questions": [
                    _minimal_question(
                        capability="scientific_analysis",
                        domain=domain,
                    )
                ],
            }
        )


def test_manifest_bank_metadata_matches_bank_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"
    manifest = yaml.safe_load((bank_root / "manifest.yaml").read_text(encoding="utf-8"))

    for entry in manifest["banks"]:
        bank_path = bank_root / entry["path"]
        raw_bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
        assert len(raw_bank["questions"]) == entry["questions"], entry["path"]

        manifest_cap = entry.get("capability")
        bank_cap = raw_bank.get("capability")
        assert bank_cap is not None, entry["path"]
        assert manifest_cap == bank_cap, entry["path"]

        manifest_domain = entry.get("domain")
        bank_domain = raw_bank.get("domain")
        assert bank_domain is not None, entry["path"]
        assert manifest_domain == bank_domain, entry["path"]


def test_manifest_banks_validate_against_v5_schema() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"
    manifest = yaml.safe_load((bank_root / "manifest.yaml").read_text(encoding="utf-8"))

    for entry in manifest["banks"]:
        bank_path = bank_root / entry["path"]
        raw_bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
        QuestionBank.model_validate(raw_bank)


def test_input_manual_helper_coverage_questions_are_outcome_based() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank" / "input_generation"
    question_ids = {
        "IG_abacus_009_20260508",
        "IG_cp2k_002_20260508",
        "IG_cp2k_003_20260508",
        "IG_gromacs_002_20260508",
    }
    forbidden_terms = {
        "input-manual-helper",
        "skill_dir",
        "render_input.py",
        "diagnose_input.py",
        "write_manifest.py",
        "references/engine_routes.md",
        "engine skill",
    }

    questions = []
    for bank_path in bank_root.glob("ig_agnostic_*.yaml"):
        raw_bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
        questions.extend(raw_bank["questions"])

    selected = [q for q in questions if q["id"] in question_ids]
    assert {q["id"] for q in selected} == question_ids

    for question in selected:
        chunks = [question["intent"], question["human_prompt_seed"]]
        chunks.extend(item["criterion"] for item in question["scoring_checklist"])
        text = "\n".join(chunks).lower()
        for forbidden in forbidden_terms:
            assert forbidden not in text, question["id"]


def test_active_question_banks_use_only_business_line_domains() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"

    for bank_path in sorted(bank_root.glob("*/*.yaml")):
        if bank_path.name == "manifest.yaml":
            continue
        raw_bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
        dom = raw_bank["domain"]
        if dom == "agnostic":
            continue
        assert dom in BUSINESS_LINE_DOMAINS, bank_path.as_posix()
        assert {q["domain"] for q in raw_bank["questions"]} == {raw_bank["domain"]}


def test_agnostic_manifest_entries_match_bank_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"
    manifest = yaml.safe_load((bank_root / "manifest.yaml").read_text(encoding="utf-8"))

    for entry in manifest["banks"]:
        if entry.get("domain") != "agnostic":
            continue
        path = bank_root / entry["path"]
        raw_bank = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw_bank["domain"] == "agnostic", entry["path"]
        assert {q["domain"] for q in raw_bank["questions"]} == {"agnostic"}, entry[
            "path"
        ]


def test_bank_yaml_filename_matches_capability_and_domain() -> None:
    """Bank file basename is ``{xx}_{domain}.yaml`` or ``{xx}_{domain}_{tag}.yaml``."""
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"

    for bank_path in sorted(bank_root.glob("*/*.yaml")):
        if bank_path.name == "manifest.yaml":
            continue
        cap_dir = bank_path.parent.name
        raw_bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
        assert raw_bank["capability"] == cap_dir, bank_path.as_posix()
        assert _bank_yaml_filename_matches_capability_domain(
            capability=raw_bank["capability"],
            domain=raw_bank["domain"],
            filename=bank_path.name,
        ), bank_path.as_posix()
        assert {q["domain"] for q in raw_bank["questions"]} == {raw_bank["domain"]}


def test_phase2_sa_semiconductor_and_wo_alloy_have_expected_question_ids() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"

    sa_semiconductor = yaml.safe_load(
        (bank_root / "scientific_analysis" / "sa_semiconductor.yaml").read_text(
            encoding="utf-8"
        )
    )
    semi_ids = [q["id"] for q in sa_semiconductor["questions"]]
    assert "WO_general_perov_007_20260417" in semi_ids

    wo_alloy = yaml.safe_load(
        (bank_root / "workflow_orchestration" / "wo_alloy.yaml").read_text(
            encoding="utf-8"
        )
    )
    wo_alloy_ids = [q["id"] for q in wo_alloy["questions"]]
    assert "WO_general_steel_008_20260417" in wo_alloy_ids


def test_phase2_split_banks_have_expected_question_ids() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"

    semiconductor_bank = yaml.safe_load(
        (bank_root / "workflow_orchestration" / "wo_semiconductor.yaml").read_text(
            encoding="utf-8"
        )
    )
    catalysis_bank = yaml.safe_load(
        (bank_root / "workflow_orchestration" / "wo_catalysis.yaml").read_text(
            encoding="utf-8"
        )
    )

    wo_semi_ids = [q["id"] for q in semiconductor_bank["questions"]]
    assert "WO_elec_001_20260411v2" in wo_semi_ids
    assert "WO_elec_009_20260415" in wo_semi_ids
    wo_cat_ids = [q["id"] for q in catalysis_bank["questions"]]
    assert "WO_elec_006_20260411v2" in wo_cat_ids
    assert "WO_elec_007_20260415" in wo_cat_ids
    assert "WO_struct_001_20260404" in wo_cat_ids
    assert "WO_struct_002_20260404" in wo_cat_ids


def test_manifest_active_totals_after_phase2_splits() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / "evaluation" / "question_bank"
    manifest = yaml.safe_load((bank_root / "manifest.yaml").read_text(encoding="utf-8"))

    assert len(manifest["banks"]) == 30
    assert sum(int(entry["questions"]) for entry in manifest["banks"]) == 162
