---
name: aissq-explorer
description: "Use this skill to query AIS Square (https://aissquare.com), a public registry that primarily hosts DP-family MLIP checkpoints (DPA, DPA-2, DPA-3, DPA-3.x-NM variants, and related DP forks) and a curated set of open MLIP training datasets (e.g. DeepEMs, OpenLAM, organic-reactions sets). AIS Square is ONE possible source - not exhaustive - and most non-DP MLIP families (e.g. MACE-MP, SevenNet, MatterSim, Orb) are typically NOT mirrored here. Reach for this skill when (a) the task plausibly needs an MLIP `.pt`/`.pth`/`.ckpt`/`.model` checkpoint or a public DFT-labeled training dataset, (b) the user has NOT pinned an external source (Hugging Face / a specific URL / a paper's project page), and (c) you would otherwise have to guess a download URL or version from memory. The skill exposes list / search / info / download against the registry's public API; if nothing is found there, report it honestly and point at the family's canonical mirror. ALWAYS invoke this skill BEFORE hand-typing an OSS object URL for an MLIP checkpoint."
skill_type: operator
---

# aissq-explorer - MLIP Asset Discovery & Download

Discover, look up and download MLIP model checkpoints and training datasets from the public AIS Square registry. No authentication is required - the listing, detail and download endpoints are all public.

> **Scope.** AIS Square is **one** of several places that host MLIP assets; it is **primarily a DP-family registry** (DPA-2.x, DPA-3, DPA-3.x-NM, and related DP variants) plus a curated set of open training datasets. Most non-DP MLIP families (MACE-MP, SevenNet, MatterSim, Orb, etc.) are NOT typically mirrored here. If the user is after one of those families, this skill is still useful for *confirming* their absence on AIS Square, but the asset itself will live elsewhere.

> **Hand-off.** This skill is for **fetching the artifact** (the `.pt`/`.pth`/`.ckpt`/`.model` file or a dataset bundle). After download, hand off to the `mlips` skill (run ASE / LAMMPS / phonon / NEB) or to a local fine-tuning workflow. This skill does NOT run inference.

## When to use

Invoke this skill when the user's task involves one of the following, **and** the source has not been pinned externally:

- A DP-family checkpoint by family name (DPA, DPA-2, DPA-3, DPA-3.x, dpa3, DPA-3.2-5M, etc.) - these are the registry's main inventory.
- A "universal", "foundation", "pretrained" or "general-purpose" MLIP checkpoint where the source is unspecified.
- Any `.pt` / `.pth` / `.ckpt` / `.model` weights file for an MLIP, source unspecified.
- "Latest version" / "current release" / "newest" of an MLIP family - discoverable from the registry's `modifyDate` field.
- A public DFT-labeled energy/force training dataset for fine-tuning a foundation model (energetic materials, organic reactions, catalysis, MOFs, etc.) where the source is unspecified.
- Confirming whether a specific non-DP family (MACE-MP, SevenNet, MatterSim, Orb, ...) is *also* mirrored on AIS Square - search first, then honestly report present or absent.

## When NOT to fabricate

If `info <name>` returns nothing and `search <keyword>` also returns nothing, do NOT invent an OSS URL or a download host. Report "not present on AIS Square" and, if relevant, point the user at the family's canonical mirror (Hugging Face / a paper's project page / official GitHub release).

## When NOT to use

- The user only wants to *run* an MLIP they already have on disk - use `mlips`.
- The user wants a crystal-structure database - use `mcp-mat-struct-db`.
- The user has already pinned a non-AIS-Square URL or a Hugging Face model ID - download from that source directly.

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
| `search <keyword> --type <models\|datasets>` | Case-insensitive substring match on name |
| `info <name> --type <models\|datasets>` | Full detail incl. files list (fileName, downloadLink, size) |
| `download <name> --type <models\|datasets> --output <dir>` | Download all files for a resource into `<dir>/<name>/` |

Add `--insecure` to disable SSL verify if the environment has cert issues.

## Standard workflow

1. **Discover** - call `list` (sort by `downloads` or `modified`) or `search <keyword>` to find candidates. Take note of `name`, `ID`, `modifyDate`, `downloadCount`.
2. **Inspect** - call `info <name> --type <...>` to get the file list with `downloadLink` and authoritative byte `size`. Record the **host** of the download URL (typically `store.aissquare.com`) as provenance evidence.
3. **Decide latest** - among multiple versions (e.g. DPA-2.4-7M vs DPA-3.1-3M vs DPA-3.2-5M), the highest version string is usually the latest; cross-check `modifyDate`.
4. **Download** - call `download <name> --type <...> --output <workdir>` and save the result. The download is streamed in 8 KB chunks; large checkpoints (>50 MB) are normal.
5. **Hand off** - for inference, use the `mlips` skill with the downloaded `.pt` path. For LAMMPS, freeze a single head first (see the `mlips` skill's "DPA + LAMMPS" section).

## Honesty constraints

- Do NOT fabricate `downloadLink`, `size`, `modifyDate`, version numbers, or DPA head lists. Every claim about an asset must come from a `list` or `info` call (or be honestly marked unknown).
- If `info <name>` returns nothing, fall back to `search <keyword>`; if that also returns nothing, report "not found on AIS Square" - do NOT guess an OSS URL.
- If a non-DP family is asked for (MACE-MP, SevenNet, MatterSim, Orb, ...), still run a search to confirm current registry state, but if absent, say so and point at the family's canonical mirror rather than inventing a link.
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

## Output JSON shape

All subcommands emit a single JSON object on stdout. Example `info`:

```json
{
  "name": "DPA-3.2-5M",
  "ID": 392,
  "type": "models",
  "modifyDate": "2025-12-29T00:00:00+08:00",
  "files": [
    {
      "fileName": "DPA-3.2-5M.pt",
      "downloadLink": "https://store.aissquare.com/models/.../DPA-3.2-5M.pt",
      "size": 64837120,
      "download_host": "store.aissquare.com"
    }
  ]
}
```

Always include `download_host` (parsed from `downloadLink`) so downstream artifacts have a clean grounding field.

## Errors

- `HTTP 4xx/5xx` from `backend.aissquare.com` -> retry once with `--insecure`; if still failing, honestly report the error.
- `code != 0` in the response body -> surface the `message` field as the error.
- Download interrupted -> the client retries up to 3 times with exponential backoff; if still failing, the partial file is left on disk and the error is reported.
