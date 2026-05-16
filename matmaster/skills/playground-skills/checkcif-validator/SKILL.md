---
name: checkcif-validator
description: "Submit a CIF file to the IUCr checkCIF/PLATON web service for crystal structure validation. Returns alert counts (A/B/C/G levels) and the full HTML report. Use after crystal structure refinement to verify CIF quality before reporting R-factors. A-level alerts indicate serious problems that must be resolved or explained."
skill_type: operator
---

# checkCIF Validator Skill

Submit a CIF file to the IUCr checkCIF web service
([https://checkcif.iucr.org](https://checkcif.iucr.org)) and retrieve the
PLATON/checkCIF validation report.

Requires: `requests` (standard in most Python environments).

## Alert Levels

| Level | Meaning |
|-------|---------|
| **A** | Serious problem — must be resolved or explicitly explained before publication |
| **B** | Potentially serious — should be investigated |
| **C** | Check — minor issue or unusual feature worth noting |
| **G** | General information — informational only |

A structure is considered publication-ready when it has **0 A-level alerts**.

## Script

### run_checkcif.py

Submit a CIF file and return the alert summary plus the full HTML report.

**Usage:**
```
python run_checkcif.py --file structure.cif [--timeout 180]
```

**Arguments:**
- `--file` — Path to the CIF file to validate (required).
- `--timeout` — HTTP request timeout in seconds (default: 180). Large CIFs
  with embedded structure factors may take longer.

**Output JSON (success):**
```json
{
  "success": true,
  "file": "structure.cif",
  "a_alerts": 0,
  "b_alerts": 0,
  "c_alerts": 1,
  "g_alerts": 12,
  "summary": "A=0 B=0 C=1 G=12",
  "report": "... full HTML report text (truncated to 20 KB) ..."
}
```

**Output JSON (failure):**
```json
{
  "success": false,
  "file": "structure.cif",
  "error": "HTTP request failed: ..."
}
```

**Common usage:**
```
python ${SKILL_DIR}/scripts/run_checkcif.py --file refined.cif
```

## Rules

- Always run this skill after obtaining a refined CIF.
- If A-level alerts are present, investigate and resolve them before
  reporting the structure as complete.
- B-level alerts should be investigated; C/G alerts are informational.
- The full HTML report contains detailed descriptions of each alert — read it
  to understand what needs to be fixed.
