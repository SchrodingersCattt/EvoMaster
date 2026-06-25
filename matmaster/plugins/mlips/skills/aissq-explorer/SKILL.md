---
name: aissq-explorer
description: "Use ONLY to search AIS Square (aissquare.com) for public MLIP checkpoints or DFT-labeled datasets when no source is given. Do NOT use for HuggingFace, figshare, GitHub, local paths, explicit URLs, or already-known models; fetch from the stated source and use mlips instead."
---

# aissq-explorer - MLIP Asset Discovery & Download

Discover, look up and download MLIP model checkpoints and training datasets from the public AIS Square registry. No authentication is required - the listing, detail and download endpoints are all public.

> **Scope.** AIS Square is **one** of several places that host MLIP assets; it is **primarily a DP-family registry** (DPA-2.x, DPA-3, DPA-3.x-NM, and related DP variants) plus a curated set of open training datasets. Most non-DP MLIP families (MACE-MP, SevenNet, MatterSim, Orb, etc.) are NOT typically mirrored here. If the user is after one of those families, this skill is still useful for *confirming* their absence on AIS Square, but the asset itself will live elsewhere.

> **Hand-off.** This skill is for **fetching the artifact** (the `.pt`/`.pth`/`.ckpt`/`.model` file or a dataset bundle). After download, hand off to the `mlips` skill (run ASE / LAMMPS / phonon / NEB) or to a local fine-tuning workflow. This skill does NOT run inference.

## API endpoints (public, no auth)

| Endpoint | Method | Returns |
|----------|--------|---------|
| `https://backend.aissquare.com/content/{models\|datasets}?page=1&pageSize=300` | GET | `{ data: { items: [...], total: N } }` |
| `https://backend.aissquare.com/dpa/detail/{models\|datasets}?id=<ID>&name=<NAME>` | GET | `{ data: { files: [{ fileName, downloadLink, size }] } }` |
| `<downloadLink>` (typically on `store.aissquare.com`) | GET | binary stream |

Item schema (listing): `ID:int, type:str, name:str, downloadCount:int, viewCount:int, prefix:str, author:str(JSON), modifyDate:str(ISO)`.
Detail schema: `ID, name, description:str(markdown), files:list[{fileName, downloadLink, size:int(bytes)}]`.

## Vendored CLI: `scripts/aissq_client.py`

A stdlib-only (urllib + json) client lives at `scripts/aissq_client.py` inside this skill. Resolve the skill directory at runtime (do not hardcode); from a workspace, do:

```bash
SKILL_DIR=$(python3 -c "import matmaster.skills, pathlib; print(pathlib.Path(matmaster.skills.__file__).parent / 'aissq-explorer')")
CLIENT="$SKILL_DIR/scripts/aissq_client.py"
python3 "$CLIENT" --help
```

Subcommands (all print JSON to stdout):

| Command | Purpose |
|---------|---------|
| `list <models\|datasets> [--page-size N] [--limit N] [--sort downloads\|modified]` | List resources, optionally sorted/truncated |
| `search <keyword> --type <models\|datasets>` | Case-insensitive substring match on **name** |
| `info <name> --type <models\|datasets>` | Metadata + files + first 1200 chars of description (safe default; full text not embedded). Add `--full` to embed full markdown, or `--grep PAT [--context N]` to return only matching context lines. |
| `grep <pattern> --type <models\|datasets> [--name-filter REGEX] [--max-items N] [--jobs N]` | Cross-corpus regex search **inside descriptions** (not just names). Returns one row per matching resource with line-numbered excerpts. Use this when the user asks for a *capability* (`fparam`, `charge`, `spin`, `phonon`, ...) rather than a known asset name. |
| `download <name> --type <models\|datasets> --output <dir>` | Download all files for a resource into `<dir>/<name>/` |

Add `--insecure` to disable SSL verify if the environment has cert issues.

## Standard workflow

1. **Discover** - call `list` (sort by `downloads` or `modified`) or `search <keyword>` (matches **names**) to find candidates. If the user asked for a *capability or feature* (e.g. "charge/spin via fparam", "trained for phonons") and you don't know which name carries it, run `grep <pattern> --type <...>` instead - it searches **descriptions** across all resources and returns line-numbered excerpts.
2. **Inspect** - call `info <name> --type <...>`. The default response includes `files` (with `downloadLink`, byte `size`, parsed `download_host`) plus a 1200-char `description_summary` and `description_length`. If you need a specific keyword or sentence from the description as evidence, prefer `info <name> --grep "<pattern>" --context 2` over `info --full`; only fall back to `--full` when no specific keyword is known.
3. **Decide latest** - among multiple versions (e.g. DPA-2.4-7M vs DPA-3.1-3M vs DPA-3.2-5M), the highest version string is usually the latest; cross-check `modifyDate`.
4. **Download** - call `download <name> --type <...> --output <workdir>` and save the result. Streams in 8 KB chunks; large checkpoints (>50 MB) are normal.
5. **Hand off** - for inference, use the `mlips` skill with the downloaded `.pt` path. For LAMMPS, freeze a single head first (see the `mlips` skill's "DPA + LAMMPS" section).

## Honesty constraints

- Do NOT fabricate `downloadLink`, `size`, `modifyDate`, version numbers, or DPA head lists. Every claim about an asset must come from a `list`, `search`, `info`, or `grep` call (or be honestly marked unknown).
- Any verbatim description excerpt used as evidence (e.g. "supports charge/spin via fparam") must come from a real `info --grep` or `grep` response - never paraphrased from memory.
- If `info <name>` returns nothing, fall back to `search <keyword>` (name match) and/or `grep <pattern>` (description match); if all of them return nothing, report "not found on AIS Square" - do NOT guess an OSS URL.
- If a non-DP family is asked for (MACE-MP, SevenNet, MatterSim, Orb, ...), still run search + grep to confirm current registry state, but if absent, say so and point at the family's canonical mirror rather than inventing a link.
- If the network is unreachable, report it explicitly. Do not hand-type `https://...oss-cn-zhangjiakou.aliyuncs.com/...` URLs as a substitute.

## Concrete examples

### Latest DPA-3 checkpoint provenance

```bash
python3 "$CLIENT" search DPA-3 --type models > /tmp/dpa3_candidates.json
python3 "$CLIENT" info "DPA-3.2-5M" --type models > /tmp/dpa3_5m_detail.json
# /tmp/dpa3_5m_detail.json["files"][0] has fileName, downloadLink, size
```

### List MLIP models sorted by recency

```bash
python3 "$CLIENT" list models --sort modified --limit 20
```

### Search for an energetic-materials training dataset

```bash
python3 "$CLIENT" search energetic --type datasets
python3 "$CLIENT" info DeepEMs-25 --type datasets
python3 "$CLIENT" download DeepEMs-25 --type datasets --output ./assets
```

### Find any MLIP that advertises charge / spin via `fparam` (capability lookup)

```bash
# Cross-corpus regex search over descriptions; returns only matching excerpts.
python3 "$CLIENT" grep "fparam" --type models --jobs 16
# Pick a hit (e.g. DPA3-Omol-Large) and pull a tight evidence quote.
python3 "$CLIENT" info DPA3-Omol-Large --type models --grep "fparam" --context 2
```

## Output JSON shape

All subcommands emit a single JSON object on stdout. Example `info` (default mode):

```json
{
  "ID": 392,
  "name": "DPA-3.2-5M",
  "type": "models",
  "modifyDate": "2025-12-29T00:00:00+08:00",
  "downloadCount": 312,
  "files": [
    {
      "fileName": "DPA-3.2-5M.pt",
      "downloadLink": "https://store.aissquare.com/models/.../DPA-3.2-5M.pt",
      "size": 64837120,
      "download_host": "store.aissquare.com"
    }
  ],
  "description_length": 11275,
  "description_summary": "<first 1200 chars of markdown description...>",
  "description_truncated": true
}
```

Example `info --grep "fparam" --context 2`:

```json
{
  "ID": 392, "name": "DPA-3.2-5M", "type": "models",
  "files": [...],
  "description_length": 11275,
  "description_grep_pattern": "fparam",
  "description_matches": [
    {"line": 87, "context": "...fparam keyword in atoms.info...\n..."}
  ]
}
```

Example `grep "fparam" --type models`:

```json
{
  "pattern": "fparam", "type": "models",
  "items_scanned": 123, "n_matches": 2, "n_errors": 0,
  "results": [
    {"ID": 398, "name": "DPA3-Omol-Large", "modifyDate": "...",
     "description_length": 8811, "n_hits": 2,
     "excerpts": [{"line": 25, "context": "..."}, ...]},
    {"ID": 392, "name": "DPA-3.2-5M", "modifyDate": "...", ...}
  ],
  "errors": []
}
```

Always include `download_host` (parsed from `downloadLink`) in downstream artifacts so they have a clean grounding field.

## Errors

- `HTTP 4xx/5xx` from `backend.aissquare.com` -> retry once with `--insecure`; if still failing, honestly report the error.
- `code != 0` in the response body -> surface the `message` field as the error.
- Download interrupted -> the client retries up to 3 times with exponential backoff; if still failing, the partial file is left on disk and the error is reported.
