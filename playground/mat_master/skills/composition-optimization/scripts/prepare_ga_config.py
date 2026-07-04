#!/usr/bin/env python3
"""prepare_ga_config.py — Generate ga_config.json and run_ga.py for bohrium-job submission.

Usage:
    python prepare_ga_config.py \
        --elements '["Fe","Ni","Co","Cr","Si"]' \
        --targets '[{"name":"TEC","type":"surrogate","model_path":"https://...","weight_mean":-1.0,"weight_std":0.0,"normalization":{"method":"z-score","params":{"mean":9.76,"std":4.30}}},{"name":"density","type":"linear_mixture","data_source":"density","weight_mean":-1.0,"weight_std":0.0,"normalization":{"method":"z-score","params":{"mean":8331.9,"std":182.2}}}]' \
        --constraints '[{"target":"Fe","condition":">=0.55"},{"target":"Fe","condition":"<=0.70"}]' \
        --output-dir /workspace/ga_input

Optional:
    --ga-params '{"population_size":20,"generations":50}'
    --init-population '[[0.63,0.29,0.06,0.02,0.00]]'
    --structure-config '{"template_path":"fcc","supercell":[5,5,5]}'
    --dry-run   (print config to stdout without writing files)
"""

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_GA_PARAMS = {
    "population_size": 20,
    "generations": 50,
    "crossover_rate": 0.8,
    "mutation_rate": 0.15,
    "selection_mode": "tournament",
}

DEFAULT_STRUCTURE_CONFIG = {
    "template_path": "fcc",
    "supercell": [5, 5, 5],
}

RUN_GA_TEMPLATE = '''#!/usr/bin/env python3
"""run_ga.py — Wrapper to invoke comp_dart GA inside the container."""
import json
import sys

from comp_dart.core.ga import GeneticAlgorithm
from comp_dart.core.fitness import WeightedAggregator
from comp_dart.core.constraints import ElementBoundConstraint, SumConstraint
from comp_dart.targets.surrogate import SurrogateModelTarget
from comp_dart.targets.linear_mixture import LinearMixtureTarget
from comp_dart.generators.template_filler import TemplateLatticeFiller

def load_config():
    with open("ga_config.json") as f:
        return json.load(f)

def build_targets(config):
    targets = []
    for t in config["targets"]:
        if t["type"] == "surrogate":
            targets.append(SurrogateModelTarget(
                models=[t["model_path"]],
                requires_structure=True,
                weight_mean=t.get("weight_mean", -1.0),
                weight_std=t.get("weight_std", 0.0),
                normalization=t.get("normalization"),
            ))
        elif t["type"] == "linear_mixture":
            targets.append(LinearMixtureTarget(
                data_source=t.get("data_source", "density"),
                weight_mean=t.get("weight_mean", -1.0),
                weight_std=t.get("weight_std", 0.0),
                normalization=t.get("normalization"),
            ))
    return targets

def build_constraints(config):
    constraints = []
    for c in config["constraints"]:
        op_map = {">=": ">=", "<=": "<=", ">": ">", "<": "<", "==": "=="}
        cond = c["condition"]
        for op in sorted(op_map.keys(), key=len, reverse=True):
            if cond.startswith(op):
                val = float(cond[len(op):])
                constraints.append(ElementBoundConstraint(c["target"], op, val))
                break
    return constraints

def main():
    config = load_config()
    targets = build_targets(config)
    constraints = build_constraints(config)

    sc = config.get("structure_config", {})
    structure_generator = TemplateLatticeFiller(
        template_path=sc.get("template_path", "fcc"),
        supercell=sc.get("supercell", [5, 5, 5]),
    )

    ga_p = config.get("ga_params", {})
    ga = GeneticAlgorithm(
        targets=targets,
        constraints=constraints,
        structure_generator=structure_generator,
        elements=config["elements"],
        population_size=ga_p.get("population_size", 20),
        generations=ga_p.get("generations", 50),
        crossover_rate=ga_p.get("crossover_rate", 0.8),
        mutation_rate=ga_p.get("mutation_rate", 0.15),
        selection_mode=ga_p.get("selection_mode", "tournament"),
        init_population=config.get("init_population"),
    )

    from comp_dart.api.endpoints import optimize_composition
    result = optimize_composition(ga)

    with open("results.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"success": True, "best_score": result.get("best_score")}, indent=2))

if __name__ == "__main__":
    main()
'''


def main():
    parser = argparse.ArgumentParser(
        description="Generate ga_config.json and run_ga.py for DART GA submission"
    )
    parser.add_argument("--elements", required=True, help="JSON array of element symbols")
    parser.add_argument("--targets", required=True, help="JSON array of target configs")
    parser.add_argument("--constraints", required=True, help="JSON array of constraints")
    parser.add_argument("--ga-params", default=None, help="JSON object of GA parameters")
    parser.add_argument("--init-population", default=None, help="JSON array of seed compositions")
    parser.add_argument("--structure-config", default=None, help="JSON object for structure config")
    parser.add_argument("--output-dir", default="./ga_input", help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Print config to stdout only")

    args = parser.parse_args()

    # Parse JSON arguments
    elements = json.loads(args.elements)
    targets = json.loads(args.targets)
    constraints = json.loads(args.constraints)
    ga_params = json.loads(args.ga_params) if args.ga_params else DEFAULT_GA_PARAMS
    structure_config = json.loads(args.structure_config) if args.structure_config else DEFAULT_STRUCTURE_CONFIG
    init_population = json.loads(args.init_population) if args.init_population else None

    # Build config
    config = {
        "elements": elements,
        "targets": targets,
        "constraints": constraints,
        "structure_config": structure_config,
        "ga_params": {**DEFAULT_GA_PARAMS, **ga_params},
    }
    if init_population:
        config["init_population"] = init_population

    if args.dry_run:
        print(json.dumps(config, indent=2))
        return

    # Write files
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config_path = out_dir / "ga_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    wrapper_path = out_dir / "run_ga.py"
    with open(wrapper_path, "w") as f:
        f.write(RUN_GA_TEMPLATE)

    print(json.dumps({
        "success": True,
        "output_dir": str(out_dir),
        "files": ["ga_config.json", "run_ga.py"],
    }, indent=2))


if __name__ == "__main__":
    main()
