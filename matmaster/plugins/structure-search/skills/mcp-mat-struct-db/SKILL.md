---
name: mcp-mat-struct-db
description: Use when retrieving known crystal structures from databases. Supports lookup by chemical formula, elements, and space group; some backends support limited band-gap filtering; returns CIF/POSCAR when available.
mcp_server: mat_struct_db
---

# Structure Database (MCP) — Query Guide

## Backend Overview

The structure retrieval service mainly uses these database backends:

| Backend | Use case |
|---------|----------|
| `bohriumpublic` | General inorganic crystal structures. Useful for semiconductors, battery materials, oxides, perovskites, catalysts, etc. |
| `optimade` | Multi-provider database aggregation, covering MP, COD, NOMAD, Alexandria, ODBX, twodmatpedia, and other providers. |
| `openlam` | Crystal structures and lattice-matching-related structures. Useful for formula, energy-range, and submission-time queries. |
| `mofdbsql` | MOF structure database. Useful for MOFs, porous materials, surface area, pore size, and void-fraction queries. |
| `corecof` | CoRE COF (Covalent Organic Framework) database. Supports topology, name, type (2D/3D), pore size (PLD/LCD), surface area, and density filters. |

For general inorganic crystal-structure retrieval, prefer:

```text
bohriumpublic -> optimade -> openlam
```

For MOF or porous-framework retrieval, prefer:

```text
mofdbsql -> optimade
```

For COF (Covalent Organic Framework) retrieval, prefer:

```text
corecof
```

## Query Construction

When building the `fetch_structures_from_db` query, prioritize chemical formula, elements, and space group.

| Available information | Query pattern |
|-----------------------|---------------|
| Formula only | `RbClO4`, `LaH10` |
| Formula plus space group | `TiO2 space group 136`, `Ti Al O space group 63` |
| Element system only | `Ti Al O`, `Na P O` |
| Specific database source | Do not encode source names in the query. Filter by provenance after results return. |
| MP/COD/ICSD-style ID | Bootstrap formula and space group first, then build the query. |

### Avoid vs Preferred

Do not put ordinary source words directly into the query, for example:

```text
TiO2 mp rutile
SiO2 only COD
```

Prefer:

```text
TiO2 space group 136
SiO2
```

Then filter returned candidates by MP, COD, or other provenance.

### Examples

| User intent | Recommended query | Notes |
|-------------|-------------------|-------|
| Find RbClO4 structure | `RbClO4` | Formula query. |
| Find TiO2 rutile from MP | `TiO2 space group 136` | Resolve rutile to space group 136. Filter MP after results return. |
| Find Ti-Al-O with space group 63 | `Ti Al O space group 63` | Elements plus space group. |
| Find SiO2 from COD | `SiO2` | Filter COD after results return. |
| Find oxide semiconductors with band gap 1 to 2 eV | `O band gap 1 2` | Supported only by selected backends or providers. |
| Find HKUST-1 or UiO-66 | Prefer `mofdbsql` | MOF-specific backend is more suitable. |
| Find COF with hcb topology | `COF hcb 拓扑` | Routes to `corecof`. |
| Find COF-LZU1 structure | `COF-LZU1` | Name-based COF lookup via `corecof`. |
| Find 3D COF with PLD > 10 Å | `3D COF 孔径大于10` | Type + pore-size filter via `corecof`. |

## CoRE COF Notes

`corecof` is a dedicated backend for the CoRE COF database (~600 structures). It supports structured filtering by:

| Filter | Description | Example |
|--------|-------------|---------|
| `topology` | Framework topology net (e.g. hcb, sql, dia, kgm) | `hcb 拓扑的COF` |
| `name` | COF name or partial match | `COF-LZU1`, `TpPa` |
| `type` | Dimensionality: `2D` or `3D` | `3D COF` |
| `pld_min` / `pld_max` | Pore Limiting Diameter (Å) | `孔径大于15埃` |
| `lcd_min` / `lcd_max` | Largest Cavity Diameter (Å) | `LCD 10-20` |
| `sa_min` / `sa_max` | Surface Area (m²/g) | `比表面积大于2000` |
| `density_min` / `density_max` | Density (g/cm³) | `密度小于0.5` |

COF queries are automatically routed to this backend when the query mentions COF, covalent organic framework, or specific COF names/topologies. Do not mix COF-specific filters with element-based inorganic queries.

## OPTIMADE Notes

`optimade` is a multi-provider aggregation backend and is useful for cross-database structure retrieval.

Ordinary structure queries mainly cover:

```text
alexandria
cmr
cod
mp
mpdd
mpds
nmd
odbx
omdb
tcod
twodmatpedia
```

Note: `oqmd` may appear in the default provider list, but its URL is currently commented out in the implementation, so it is usually skipped.

OPTIMADE capability boundaries:

- Basic queries support formula and elements.
- Space-group queries are supported, but provider coverage is incomplete.
- Band-gap queries are supported only for selected providers, mainly `alexandria`, `odbx`, and `twodmatpedia`.
- Do not claim that all OPTIMADE providers support band-gap filtering.

## Backend Selection

| User intent | Preferred backend |
|-------------|-------------------|
| General inorganic crystal structure | `bohriumpublic`, `optimade`, `openlam` |
| Materials Project / MP / COD / NOMAD source request | `optimade` |
| Semiconductor or band-gap material | `bohriumpublic` or `optimade` |
| Battery material | `bohriumpublic`, `openlam`, `optimade` |
| MOF / porous material | `mofdbsql`, `optimade` |
| COF (Covalent Organic Framework) | `corecof` |
| Explicit OpenLAM request | `openlam` |
| Explicit Bohrium Public request | `bohriumpublic` |

## ID Bootstrap

For `mp-XXXX`, `icsd-XXXX`, `cod-XXXX`, or similar ID-driven requests, do not infer structure metadata from memory.

Recommended workflow:

1. Use WebSearch or an official source to confirm the formula and space group.
2. If the space group is known, query: `<formula> space group <sg>`
3. If the space group is unknown, query: `<formula>`
4. After results return, filter candidates by the requested database provenance.


## Failure Handling

If the database returns nothing, state this explicitly. Do not fabricate:

- lattice constants
- space group
- Wyckoff positions
- CIF/POSCAR files
- database provenance
- band gaps
- formation energies

If downloaded content contains only `summary.json`, it may not include direct CIF/POSCAR files. In that case, try to extract lattice parameters, space group, and Wyckoff positions from metadata, and only build locally with `pymatgen Structure.from_spacegroup(...)` when enough information is available.

## Efficiency Rules

- Use 1–2 attempts per target structure.
- After failure, retry once with a different formula notation or space-group expression.
- For batch structure tasks, avoid spending too long on a single entry.
- Partial results are acceptable, but report which structures were found and which were not.
- Distinguish the structure source: directly retrieved from database, filtered from candidates, locally constructed, found in literature, or estimated.
