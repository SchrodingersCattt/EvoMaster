---
name: compliance-guardian
description: "Use before executing restricted software, risky shell commands, or sensitive technical guidance; checks software licensing, task safety, and research ethics, suggesting allowed alternatives when blocked."
skill_type: operator
---

# Compliance Guardian Skill

A mandatory filter for sensitive operations. It acts as a gatekeeper for:

1. **Commercial / restricted software**: Prevents unauthorized local execution of VASP, Gaussian. Writing input files is allowed; running the binary is not. Suggests ABACUS (for VASP) or remote submission; Gaussian suggests ORCA (when available) or remote.
2. **Research safety / dual-use**: Distinguishes theoretical research (allowed) from practical manufacturing/synthesis of dangerous substances (restricted). Energetic materials: DFT, detonation physics, literature, crystal structure — allowed. Synthesis recipes, formulation ratios, step-by-step manufacturing — denied. Drugs/toxins: interaction simulation allowed; synthesis denied.
3. **System security**: Blocks dangerous shell commands (e.g. rm -rf /, destructive syscalls).

## Script

- Run `${SKILL_DIR}/scripts/check_compliance.py` with two arguments: plan description and intended command (quote if containing spaces).
  - Example: `python ${SKILL_DIR}/scripts/check_compliance.py "optimize structure with VASP locally" "vasp_std"`
  - Output: JSON string with `allowed` (bool), `reason` (str), `suggestion` (str).

## Rules

- **VASP / Gaussian**: Local execution is ALWAYS denied. Allowed: writing INCAR/INPUT files, analyzing outputs. Suggest ABACUS (VASP alternative) or remote submission; for Gaussian suggest ORCA or remote.
- **Energetic materials**: Theoretical calculation (DFT, MD, detonation velocity, stability) and literature review are ALLOWED. Synthesis recipes, manufacturing processes, formulation ratios, or weaponization details are DENIED.
- **Drugs / toxins**: Interaction simulation and property calculation are ALLOWED. Synthesis or procurement details are DENIED.
- **Dangerous commands**: Commands that risk system integrity (e.g. rm -rf /, raw disk, credential abuse) are BLOCKED.
