---
name: best-practice-evaluator
description: "Evaluate computational materials science workflows, DFT setups, and experimental protocols against established best practices. Produces structured assessment reports with compliance scores, identified deviations, and improvement recommendations. Load for any 'best practice', 'protocol evaluation', or 'setup review' task."
skill_type: operator
---

# Best-Practice Evaluator Skill

Structured evaluation of computational materials science workflows against established community best practices.

## Trigger Conditions

- Task asks to evaluate whether a setup follows best practices
- Task asks to review/audit a computational protocol or workflow
- Task mentions "best practice", "protocol compliance", "setup review", "convergence testing"
- Task asks to compare a setup against standard recommendations

## Evaluation Framework

### Step 1: Identify the Domain and Method
Classify the task into one or more categories:
- **DFT setup** (basis set, functional, k-points, cutoffs, convergence)
- **Structure preparation** (cell choice, symmetry, vacuum, passivation)
- **Workflow design** (multi-step pipelines, file management, reproducibility)
- **Post-processing** (band structure, DOS, property extraction)
- **MD simulation** (ensemble, timestep, equilibration, sampling)
- **Experimental protocol** (XRD, spectroscopy, synthesis conditions)

### Step 2: Apply Domain-Specific Checklist
For each category, evaluate against the checklist below. **Score each item as compliant / partially compliant / non-compliant with specific evidence.**

### Step 3: Write Deliverables
Produce ALL requested deliverable files. Common formats:
- **Markdown report** (`*_best_practices.md` or as task specifies): structured sections per evaluation dimension, evidence-based assessment, improvement recommendations
- **JSON summary** (`*_assessment.json` or as task specifies): machine-readable scores and findings
- **Always write files before reporting completion**

## DFT Best Practice Checklist

### Basis Set & Cutoff
- [ ] Plane-wave cutoff or basis set quality appropriate for the system (≥100 Ry for ABACUS NCPP, ≥60 Ry for QE ultrasoft, etc.)
- [ ] Convergence test performed or referenced for energy cutoff
- [ ] Pseudopotential / basis set vintage and source documented

### K-point Sampling
- [ ] K-mesh density appropriate for cell size (rule of thumb: k × a ≳ 25–40 Å for metals, 20–30 for insulators)
- [ ] Convergence test performed or referenced for k-mesh
- [ ] Symmetry-reduced mesh used when appropriate
- [ ] Slab calculations: 1 k-point in vacuum direction

### Exchange-Correlation Functional
- [ ] Functional choice justified for the property of interest (PBE for structure, HSE/PBE0 for band gaps, r²SCAN for energetics, etc.)
- [ ] Known limitations of the chosen functional acknowledged (e.g., PBE band gap underestimation)
- [ ] Dispersion corrections included where relevant (DFT-D3, vdW-DF for layered materials, molecular crystals)

### SCF Convergence
- [ ] SCF convergence threshold tight enough (≤1e-6 eV for energies, ≤1e-7 for forces/stress)
- [ ] Mixing parameters appropriate for system type (lower mixing_beta for magnetic systems)
- [ ] Sufficient max SCF iterations

### Geometry Optimization
- [ ] Force convergence threshold documented (typical: 0.01–0.03 eV/Å)
- [ ] Stress convergence threshold for cell optimization (typical: 0.5 kbar)
- [ ] Optimizer choice appropriate (BFGS for stable systems, CG for difficult cases)

### Spin & Magnetism
- [ ] Spin polarization enabled for magnetic systems (nspin=2 or noncollinear)
- [ ] Initial magnetic moments physically reasonable
- [ ] Spin-orbit coupling included when relevant (heavy elements, topological properties)

## MD Best Practice Checklist

- [ ] Timestep ≤ 1–2 fs (standard), ≤ 0.5 fs for hydrogen-containing or high-temperature systems
- [ ] Equilibration period sufficient before production (typically 10–100 ps depending on system)
- [ ] Thermostat/barostat choice appropriate (Nosé-Hoover for NVT, Parrinello-Rahman for NPT)
- [ ] Energy conservation monitored (NVE) or temperature fluctuations within expected range (NVT)
- [ ] Production run length sufficient for the property of interest

## Structure Preparation Checklist

- [ ] Correct cell type used (primitive vs conventional, as required by the calculation)
- [ ] Vacuum gap sufficient for slab/molecule (≥15 Å for slabs, ≥20 Å for work function)
- [ ] Passivation applied to dangling bonds when needed
- [ ] Supercell size sufficient for defects (≥10 Å between periodic images)
- [ ] Lattice parameters from reliable source (experimental or optimized)

## Workflow Design Checklist

- [ ] Multi-step workflows properly chained (SCF → NSCF, relaxation → property calculation)
- [ ] Consistent parameters across comparable calculations (same cutoff, k-mesh density, functional)
- [ ] Intermediate results saved and verified
- [ ] Error handling for failed steps (convergence failure, file missing)

## Report Structure Template

When writing best-practice assessment reports, use this structure:

```markdown
# Best Practice Assessment: [Topic]

## Summary
- Overall compliance: [High/Medium/Low]
- Critical deviations: [count]
- Recommendations: [count]

## Evaluation by Category

### [Category 1 Name]
**Compliance**: [Compliant / Partially Compliant / Non-Compliant]
**Evidence**: [Specific observations from the setup]
**Recommendations**: [What to improve]

### [Category 2 Name]
...

## Critical Issues
[List any deviations that could invalidate results]

## Improvement Recommendations
[Prioritized list of changes to achieve best-practice compliance]
```

## JSON Assessment Template

```json
{
  "overall_compliance": "high|medium|low",
  "categories": [
    {
      "name": "category_name",
      "score": 0-100,
      "status": "compliant|partial|non_compliant",
      "findings": ["finding1", "finding2"],
      "recommendations": ["rec1", "rec2"]
    }
  ],
  "critical_issues": ["issue1"],
  "summary": "Brief overall assessment"
}
```

## Hard Constraints

- **Evidence-based**: Every compliance judgment must cite specific evidence from the evaluated setup. Do not make generic statements without connecting to the actual parameters/files being reviewed.
- **Quantitative when possible**: State actual values vs recommended ranges (e.g., "ecutwfc=50 Ry, recommended ≥100 Ry for NCPP").
- **Constructive**: Always provide specific improvement recommendations, not just "this is wrong."
- **Domain-aware**: Apply the correct best practices for the specific software, material system, and property being studied.
- **Deliverables first**: Write ALL requested files before reporting completion. An imperfect assessment delivered is better than a perfect assessment not written to disk.
- **All requested file formats**: If the task specifies JSON and Markdown, produce BOTH. If specific key names are requested for JSON, include ALL of them as top-level keys.
