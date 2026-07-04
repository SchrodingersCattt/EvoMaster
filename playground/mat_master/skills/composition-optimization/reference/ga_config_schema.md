# GA Config JSON Schema

The `ga_config.json` file configures the DART genetic algorithm optimizer.

## Top-Level Fields

```json
{
  "elements": ["Fe", "Ni", "Co", "Cr", "Si"],
  "targets": [...],
  "constraints": [...],
  "structure_config": {...},
  "ga_params": {...},
  "init_population": [...]
}
```

## Field Reference

### elements (required)
Array of element symbols. Order matters — composition vectors follow this order.

### targets (required)
Array of optimization objectives. Each target has:

| Field | Type | Description |
|---|---|---|
| `name` | string | Human-readable name (e.g., "TEC", "density") |
| `type` | string | `"surrogate"` or `"linear_mixture"` |
| `model_path` | string | OSS URL to model zip (surrogate only) |
| `data_source` | string | Property key (linear_mixture only, e.g., "density") |
| `weight_mean` | float | Weight for mean prediction (-1.0 = minimize) |
| `weight_std` | float | Weight for uncertainty (>0 = exploration bonus) |
| `normalization` | object | `{"method": "z-score", "params": {"mean": X, "std": Y}}` |

### constraints (required)
Array of composition bounds:

```json
{"target": "Fe", "condition": ">=0.55"}
{"target": "Al", "condition": "<=0.015"}
```

Supported operators: `>=`, `<=`, `>`, `<`, `==`.

### structure_config (required for surrogate targets)

```json
{
  "template_path": "fcc",
  "supercell": [5, 5, 5]
}
```

`template_path`: `"fcc"`, `"bcc"`, `"hcp"`, or path to CIF file.

### ga_params (optional, has defaults)

| Field | Default | Description |
|---|---|---|
| `population_size` | 20 | Individuals per generation |
| `generations` | 50 | Number of generations |
| `crossover_rate` | 0.8 | Crossover probability |
| `mutation_rate` | 0.15 | Mutation probability |
| `selection_mode` | "tournament" | `"tournament"` or `"roulette"` |

### init_population (optional)
Seed compositions as fraction arrays (same order as `elements`):

```json
[[0.63, 0.29, 0.06, 0.02, 0.00], ...]
```

If omitted, GA generates random initial population within constraints.

## Output

The GA writes results to `results.json` in the working directory:

```json
{
  "best_individual": [0.61, 0.27, 0.05, 0.02, 0.05],
  "best_score": -1.23,
  "pareto_front": [...],
  "generation_history": [...]
}
```
