#!/usr/bin/env python3
"""parse_ga_results.py — Parse DART GA output into ranked compositions.

Usage:
    python parse_ga_results.py \
        --input /workspace/ga_results \
        --output /workspace/ranked_compositions.json

Expects the GA result directory to contain `results.json` (produced by run_ga.py).
"""

import argparse
import json
import sys
from pathlib import Path


def parse_results(result_dir: Path) -> dict:
    """Parse GA results directory and extract ranked compositions."""
    results_file = result_dir / "results.json"

    if not results_file.exists():
        # Try to find results in subdirectories (bohrium-job extracts to subdir)
        candidates = list(result_dir.rglob("results.json"))
        if candidates:
            results_file = candidates[0]
        else:
            return {
                "success": False,
                "error": f"No results.json found in {result_dir}",
                "searched_paths": [str(p) for p in result_dir.rglob("*")][:20],
            }

    with open(results_file) as f:
        raw = json.load(f)

    # Extract key fields
    output = {
        "success": True,
        "source": str(results_file),
        "best_individual": raw.get("best_individual"),
        "best_score": raw.get("best_score"),
    }

    # Parse Pareto front if available
    pareto = raw.get("pareto_front", [])
    if pareto:
        output["pareto_front"] = pareto
        output["num_pareto_solutions"] = len(pareto)

    # Parse generation history summary
    history = raw.get("generation_history", [])
    if history:
        output["total_generations"] = len(history)
        output["final_best_score"] = history[-1].get("best_score") if history else None

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Parse DART GA results into ranked compositions"
    )
    parser.add_argument("--input", required=True, help="GA results directory")
    parser.add_argument("--output", default=None, help="Output JSON path (default: stdout)")

    args = parser.parse_args()
    result_dir = Path(args.input)

    if not result_dir.exists():
        print(json.dumps({"success": False, "error": f"Directory not found: {result_dir}"}))
        sys.exit(1)

    parsed = parse_results(result_dir)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(parsed, f, indent=2)
        print(json.dumps({"success": True, "output": str(out_path)}))
    else:
        print(json.dumps(parsed, indent=2))


if __name__ == "__main__":
    main()
