#!/usr/bin/env python
"""Migrate a legacy question-pool zip into MATTER v5 candidates.

Legacy archives (``battery.zip`` / ``2D_materials.zip`` / ``qa_mm_v1.zip`` ...)
are pre-migration snapshots of the eval question bank: one YAML per question,
organised under ``<capability>/`` folders, but using the *old* taxonomy where
``domain`` holds a **software/method name** (``vasp`` / ``pymatgen`` /
``chgnet`` / ``gpumd`` ...) instead of one of the six v5 business lines. The
verifier dialect and scoring model are otherwise the same lineage as the
current bank.

This script does a best-effort, *review-oriented* conversion. It NEVER writes
into ``evaluation/question_bank/`` directly; instead it emits candidate bank
files into a staging directory plus a human review report, so a person can
re-bucket the ``domain`` calls and merge selectively.

What it does per question:
  * ``domain``: software-name -> business line via a keyword heuristic over the
    six v5 business lines (battery / semiconductor / catalysis / alloy /
    polymer), recording matched terms. Falls back to ``agnostic`` when no
    business-line signal is found. **These calls are heuristic and must be
    reviewed** (see ``review.csv``: ``assigned_domain`` + ``matched_domain_terms``).
  * ``tags``: normalised to the controlled :class:`QuestionTag` vocabulary
    (software -> ``eng_*`` / ``code_mlip``; unknown topical tags dropped).
  * ``verify``: legacy ``token_budget_total`` -> ``token_budget``.
  * ``weight``: drops ``null`` weights and clamps negatives (schema requires >=0).
  * strips legacy extra keys on ``reference_answers`` / ``data_files``.
  * validates every converted question against the real
    :class:`evaluation.core.schemas.QuestionItem`.

Run with the project venv (needs pydantic):

    .venv/bin/python evaluation/scripts/migrate_legacy_bank.py \
        --src-zip ~/Downloads/2D_materials.zip
    # -> evaluation/migration_candidates/2D_materials/{*.yaml,review.csv,REVIEW.md}
"""

from __future__ import annotations

import argparse
import collections
import csv
import difflib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.core.question_tags import QuestionTag  # noqa: E402
from evaluation.core.schemas import QuestionItem  # noqa: E402

VALID_TAGS = {t.value for t in QuestionTag}

# Canonical 2-letter filename prefixes (mirror of
# ``tests/evaluation/capability_abbrev.CAPABILITY_TO_TWO_LETTER``).
CAPABILITY_TO_TWO_LETTER = {
    "batch_processing": "bp",
    "data_diagnosis": "dd",
    "execution_contract": "ec",
    "input_generation": "ig",
    "safety_refusal": "sf",
    "scientific_analysis": "sa",
    "structure_construction": "sc",
    "structure_retrieval": "rt",
    "workflow_orchestration": "wo",
}


def bank_yaml_basename(*, capability: str, domain: str) -> str:
    prefix = CAPABILITY_TO_TWO_LETTER[capability]
    return f"{prefix}_{domain}.yaml"


# Legacy free-form / software tag -> canonical controlled tag. Anything not
# listed here and not already a valid tag is DROPPED (and reported).
TAG_REMAP: dict[str, str] = {
    # engines
    "vasp": "eng_vasp",
    "eng_vasp": "eng_vasp",
    "abacus": "eng_abacus",
    "eng_abacus": "eng_abacus",
    "qe": "eng_qe",
    "quantum_espresso": "eng_qe",
    "eng_qe": "eng_qe",
    "lammps": "eng_lammps",
    "eng_lammps": "eng_lammps",
    "gpumd": "eng_gpumd",
    "eng_gpumd": "eng_gpumd",
    "cp2k": "eng_cp2k",
    "eng_cp2k": "eng_cp2k",
    "gromacs": "eng_gromacs",
    "eng_gromacs": "eng_gromacs",
    "orca": "eng_orca",
    "eng_orca": "eng_orca",
    # machine-learning interatomic potentials
    "mlip": "code_mlip",
    "chgnet": "code_mlip",
    "eng_chgnet": "code_mlip",
    "matgl": "code_mlip",
    "m3gnet": "code_mlip",
    "mace": "code_mlip",
    "nequip": "code_mlip",
    "dp": "code_mlip",
    "deepmd": "code_mlip",
    "deeppmd": "code_mlip",
    "nep": "code_mlip",
    # structure operations
    "struct_surface": "struct_surface",
    "surface": "struct_surface",
    "slab": "struct_surface",
    "struct_build": "struct_build",
    "struct_transform": "struct_transform",
    "struct_molcrys": "struct_molcrys",
    "struct_inspect": "struct_inspect",
    # md post-processing / characterisation / analysis
    "analysis_post_md": "analysis_post_md",
    "analysis_data": "analysis_data",
    "diffraction": "char_diffraction",
    "xrd": "char_diffraction",
    "char_diffraction": "char_diffraction",
    "char_electrochem": "char_electrochem",
    "electrochem": "char_electrochem",
    "char_battery_cycling": "char_battery_cycling",
    "cycling": "char_battery_cycling",
    # grounding / database behaviour
    "materials_project": "meta_database",
    "meta_database": "meta_database",
    "meta_grounding": "meta_grounding",
}

# v5 business-line keyword heuristics (substring match against
# id + intent + prompt + raw tags, lower-cased). Order matters: ties are broken
# by first-defined business line. ``agnostic`` is the fallback when nothing hits.
DOMAIN_TERMS: dict[str, list[str]] = {
    "battery": [
        "cathode",
        "anode",
        "electrolyte",
        "intercalat",
        "deintercalat",
        "battery",
        "li-ion",
        "na-ion",
        "lithium",
        "sodium-ion",
        "coulombic",
        "half-cell",
        "full-cell",
        "electrode",
        "voltage",
        "capacity",
        "solid electrolyte",
        "solid-electrolyte",
        "drx",
        "licoo2",
        "lifepo4",
        "lipf6",
        "nasicon",
        "olivine",
        "spinel",
        "rocksalt",
        "nernst",
        "dendrite",
        "migration barrier",
        "ionic conductivity",
        "mno2",
    ],
    "semiconductor": [
        "gaas",
        "gan",
        "semiconductor",
        "band gap",
        "bandgap",
        "band structure",
        "defect",
        "gnr",
        "graphene",
        "nanoribbon",
        "hbn",
        "h-bn",
        "boron nitride",
        "2d material",
        "monolayer",
        "mos2",
        "wse2",
        "transistor",
        "carrier concentration",
        "doping",
        "vacancy",
        "photovolt",
        "optoelectron",
    ],
    "catalysis": [
        "cataly",
        "adsorp",
        " oer",
        " orr",
        "co2rr",
        " her ",
        "surface reaction",
        "active site",
        "overpotential",
        "reaction pathway",
    ],
    "alloy": [
        "alloy",
        "hea ",
        "high-entropy",
        "high entropy",
        "intermetallic",
        "solid solution",
        "precipitat",
    ],
    "polymer": [
        "polymer",
        "monomer",
        "oligomer",
        "polyethylene",
        "polymeric",
        "chain conformation",
    ],
}

# Default budget references injected when a legacy question has the efficiency
# checklist item but forgot the matching reference entry (schema requires a ref
# for turn_budget / duration_budget). Values are loose ceilings; reviewers tune.
DEFAULT_BUDGET_BY_VERIFY = {
    "turn_budget": {"max": 30},
    "duration_budget": {"max": 7200000},
    "token_budget": {"max": 8000},
}

REF_ALLOWED_KEYS = {
    "key",
    "value",
    "tolerance",
    "unit",
    "tool_name",
    "tool_arg",
    "workspace_resolve",
}
DATAFILE_ALLOWED_KEYS = {"key", "path", "oss_url", "description"}
VERIFY_REMAP = {"token_budget_total": "token_budget"}


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def normalise_tags(raw: Any, capability: str, domain: str) -> tuple[list[str], list[str]]:
    """Return (kept_canonical_tags, dropped_raw_tags)."""
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return kept, dropped
    for item in raw:
        tag = str(item).strip()
        if not tag:
            continue
        canonical = TAG_REMAP.get(tag, tag if tag in VALID_TAGS else None)
        if canonical is None or canonical not in VALID_TAGS:
            dropped.append(tag)
            continue
        # schema forbids a tag equal to the question capability or domain
        if canonical in (capability, domain):
            dropped.append(tag)
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        kept.append(canonical)
    return kept, dropped


def detect_domain(text: str) -> tuple[str, list[str]]:
    """Heuristically map free text to one of the six v5 business lines.

    Returns ``(domain, matched_terms)``. Picks the business line with the most
    matched terms; ties are broken by ``DOMAIN_TERMS`` definition order. Falls
    back to ``agnostic`` with no matches.
    """
    low = text.lower()
    scores: dict[str, list[str]] = {}
    for dom, terms in DOMAIN_TERMS.items():
        hits = sorted({t.strip() for t in terms if t in low})
        if hits:
            scores[dom] = hits
    if not scores:
        return "agnostic", []
    best = max(scores, key=lambda d: len(scores[d]))
    return best, scores[best]


def clean_reference_answers(refs: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(refs, list):
        return out
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        cleaned = {k: v for k, v in ref.items() if k in REF_ALLOWED_KEYS}
        # legacy entries sometimes carry filename/checks at top level instead
        # of inside value; fold them in so the verifier still has its payload.
        if "value" not in cleaned:
            folded = {k: v for k, v in ref.items() if k in ("filename", "checks")}
            cleaned["value"] = folded or ref.get("value")
        out.append(cleaned)
    return out


def clean_data_files(files: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(files, list):
        return out
    for entry in files:
        if not isinstance(entry, dict):
            continue
        out.append({k: v for k, v in entry.items() if k in DATAFILE_ALLOWED_KEYS})
    return out


def clean_checklist(checklist: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(checklist, list):
        return out
    for item in checklist:
        if not isinstance(item, dict):
            continue
        new = dict(item)
        verify = new.get("verify")
        if verify in VERIFY_REMAP:
            new["verify"] = VERIFY_REMAP[verify]
        if "weight" in new:
            w = new["weight"]
            if w is None:
                new.pop("weight")
            else:
                try:
                    wf = float(w)
                    new["weight"] = max(wf, 0.0)
                except (TypeError, ValueError):
                    new.pop("weight")
        out.append(new)
    return out


def convert_question(raw: dict, src_file: str) -> tuple[dict, dict]:
    capability = raw.get("capability")
    text = " ".join(
        str(raw.get(k) or "")
        for k in ("id", "intent", "human_prompt_seed")
    )
    text += " " + " ".join(str(t) for t in (raw.get("tags") or []))
    domain, hits = detect_domain(text)
    kept_tags, dropped_tags = normalise_tags(raw.get("tags"), capability, domain)
    refs = clean_reference_answers(raw.get("reference_answers"))
    checklist = clean_checklist(raw.get("scoring_checklist"))

    # inject default budget refs for efficiency items that lack a matching ref
    ref_keys = {r.get("key") for r in refs}
    injected_budgets: list[str] = []
    for item in checklist:
        verify = item.get("verify")
        item_id = item.get("id")
        default = DEFAULT_BUDGET_BY_VERIFY.get(verify)
        if default is not None and item_id not in ref_keys:
            refs.append({"key": item_id, "value": dict(default)})
            ref_keys.add(item_id)
            injected_budgets.append(f"{item_id}({verify})")

    converted = {
        "id": raw.get("id"),
        "capability": capability,
        "domain": domain,
        "intent": raw.get("intent"),
        "human_prompt_seed": raw.get("human_prompt_seed"),
        "tags": kept_tags,
        "data_files": clean_data_files(raw.get("data_files")),
        "reference_answers": refs,
        "scoring_checklist": checklist,
    }
    meta = {
        "src_file": src_file,
        "legacy_domain": raw.get("domain"),
        "assigned_domain": domain,
        "domain_terms": hits,
        "dropped_tags": dropped_tags,
        "kept_tags": kept_tags,
        "injected_budget_refs": injected_budgets,
    }
    return converted, meta


def collect_current_bank(bank_dir: Path) -> tuple[set[str], dict[str, list[str]]]:
    ids: set[str] = set()
    seeds_by_cap: dict[str, list[str]] = collections.defaultdict(list)
    for path in bank_dir.rglob("*.yaml"):
        if "data" in path.parts or path.name in ("manifest.yaml", "eval_slices.yaml"):
            continue
        try:
            doc = load_yaml(path)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        for q in doc.get("questions") or []:
            if not isinstance(q, dict):
                continue
            ids.add(q.get("id"))
            seed = " ".join((q.get("human_prompt_seed") or "").lower().split())
            seeds_by_cap[q.get("capability")].append(seed)
    return ids, seeds_by_cap


def nearest_similarity(seed: str, pool: list[str]) -> float:
    ns = " ".join(seed.lower().split())
    best = 0.0
    for other in pool:
        ratio = difflib.SequenceMatcher(None, ns, other).ratio()
        if ratio > best:
            best = ratio
    return round(best, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--src-zip",
        required=True,
        help="Path to a legacy question-pool zip (e.g. ~/Downloads/2D_materials.zip)",
    )
    ap.add_argument(
        "--pack-name",
        default=None,
        help="Logical pack name (defaults to the zip filename stem); used for "
        "the report title and the default output dir",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output staging dir (defaults to evaluation/migration_candidates/<pack-name>)",
    )
    ap.add_argument(
        "--bank-dir",
        default=str(REPO_ROOT / "evaluation/question_bank"),
        help="Current question bank (for ID + near-duplicate checks)",
    )
    ap.add_argument(
        "--dup-threshold",
        type=float,
        default=0.6,
        help="Flag candidates whose prompt similarity to an existing same-capability question >= this",
    )
    args = ap.parse_args()

    src_zip = Path(args.src_zip).expanduser()
    pack_name = args.pack_name or src_zip.stem
    out_dir = Path(args.out) if args.out else REPO_ROOT / "evaluation/migration_candidates" / pack_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cur_ids, cur_seeds = collect_current_bank(Path(args.bank_dir))

    # read every YAML from the zip
    raw_questions: list[tuple[dict, str]] = []
    with zipfile.ZipFile(src_zip) as zf:
        for name in zf.namelist():
            if not name.endswith((".yaml", ".yml")):
                continue
            doc = yaml.safe_load(zf.read(name))
            if isinstance(doc, dict) and "questions" in doc:
                items = doc["questions"]
            elif isinstance(doc, dict) and "id" in doc:
                items = [doc]
            else:
                items = [doc] if isinstance(doc, dict) else []
            for q in items:
                if isinstance(q, dict):
                    raw_questions.append((q, name))

    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    review_rows: list[dict] = []
    seen_ids: set[str] = set()
    stats = collections.Counter()

    for raw, src_file in raw_questions:
        converted, meta = convert_question(raw, src_file)
        qid = converted["id"]
        status = "ok"
        reason = ""

        # id collisions
        if qid in cur_ids:
            status, reason = "skip", "id already in current bank"
        elif qid in seen_ids:
            status, reason = "skip", "duplicate id within archive"
        else:
            try:
                QuestionItem.model_validate(converted)
            except Exception as exc:  # noqa: BLE001
                status = "invalid"
                reason = str(exc).splitlines()[0][:300]

        sim = nearest_similarity(
            converted.get("human_prompt_seed") or "",
            cur_seeds.get(converted["capability"], []),
        )
        near_dup = sim >= args.dup_threshold

        if status == "ok":
            seen_ids.add(qid)
            grouped[(converted["capability"], converted["domain"])].append(converted)
        stats[status] += 1
        if near_dup and status == "ok":
            stats["near_dup_flagged"] += 1

        review_rows.append(
            {
                "id": qid,
                "src_file": src_file,
                "capability": converted["capability"],
                "legacy_domain": meta["legacy_domain"],
                "assigned_domain": meta["assigned_domain"],
                "matched_domain_terms": ";".join(meta["domain_terms"]),
                "kept_tags": ";".join(meta["kept_tags"]),
                "dropped_tags": ";".join(meta["dropped_tags"]),
                "injected_budget_refs": ";".join(meta["injected_budget_refs"]),
                "data_file_refs": len(converted.get("data_files") or []),
                "nearest_existing_similarity": sim,
                "near_duplicate_flag": near_dup,
                "status": status,
                "reason": reason,
            }
        )

    # write candidate bank files grouped by (capability, domain)
    written_files: list[str] = []
    for (capability, domain), questions in sorted(grouped.items()):
        cap_dir = out_dir / capability
        cap_dir.mkdir(parents=True, exist_ok=True)
        fname = bank_yaml_basename(capability=capability, domain=domain)
        bank_doc = {
            "version": "v5",
            "capability": capability,
            "domain": domain,
            "questions": questions,
        }
        target = cap_dir / fname
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                bank_doc,
                fh,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
                width=100,
            )
        written_files.append(str(target.relative_to(REPO_ROOT)))

    # write review report (CSV + markdown summary)
    csv_path = out_dir / "review.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(review_rows[0].keys()))
        writer.writeheader()
        writer.writerows(review_rows)

    cap_dom = collections.Counter(
        (r["capability"], r["assigned_domain"]) for r in review_rows if r["status"] == "ok"
    )
    md = io.StringIO()
    md.write(f"# Legacy {pack_name} -> MATTER v5 migration candidates\n\n")
    md.write(f"- source archive: `{src_zip}`\n")
    md.write(f"- total legacy questions read: **{len(raw_questions)}**\n")
    md.write(f"- converted OK (validated against QuestionItem): **{stats['ok']}**\n")
    md.write(f"- skipped (dup id): **{stats['skip']}**\n")
    md.write(f"- invalid after conversion: **{stats['invalid']}**\n")
    md.write(f"- near-duplicate flagged (sim >= {args.dup_threshold}): **{stats['near_dup_flagged']}**\n")
    n_with_data = sum(1 for r in review_rows if r["data_file_refs"])
    n_data_refs = sum(r["data_file_refs"] for r in review_rows)
    md.write(
        f"- reference input data: the archive ships **no data files** (YAML only); "
        f"**{n_with_data}** questions reference **{n_data_refs}** `data_files` that must be "
        f"sourced/regenerated (MP id / DOI / local file) before they can actually run\n\n"
    )
    md.write("## OK candidates by (capability, assigned_domain)\n\n")
    for (cap, dom), n in sorted(cap_dom.items()):
        md.write(f"- `{cap}` / `{dom}`: {n}\n")
    md.write("\n## Output candidate bank files\n\n")
    for f in written_files:
        md.write(f"- `{f}`\n")
    md.write("\n## How to review\n\n")
    md.write(
        "1. Open `review.csv`: the `assigned_domain` is a **keyword heuristic** "
        "(see `matched_domain_terms`); re-bucket any mislabelled rows by hand.\n"
        "2. Inspect `dropped_tags` for any signal worth re-adding to the "
        "controlled vocabulary (prefer skill-aligned tags).\n"
        "3. Resolve `invalid` rows (see `reason`).\n"
        "4. Re-check `near_duplicate_flag` rows against existing questions.\n"
        "5. For rows with `data_file_refs` > 0, provide the actual input files "
        "(the archive has none) and fix `data_files[].path` to "
        "`data/<question_id>/...` before the question can run.\n"
        "6. Merge accepted questions into `evaluation/question_bank/<capability>/` "
        "and update `manifest.yaml` counts (new IDs -> bump there).\n"
    )
    md_path = out_dir / "REVIEW.md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md.getvalue())

    # machine-readable summary too
    with open(out_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(
            {
                "pack": pack_name,
                "stats": dict(stats),
                "by_capability_domain": {f"{c}/{d}": n for (c, d), n in cap_dom.items()},
                "written_files": written_files,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    print(md.getvalue())
    print(f"\nwrote: {csv_path}")
    print(f"wrote: {md_path}")
    print(f"wrote: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
