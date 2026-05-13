---
name: aissq-explorer
description: "MUST use this skill whenever the task involves finding, downloading, looking up, listing, comparing, or version-checking a pretrained MLIP / universal interatomic potential model checkpoint (DPA, DPA-3, DPA-3.1-3M, DPA-3.2-5M, DPA-2.4-7M, MACE-MP, MACE-MP-0, SevenNet, MatterSim, etc.) or an open MLIP training dataset (e.g. DeepEMs, OMat24-style, organic reactions, ODAC23) — including: resolving the current latest model version; listing or searching available pretrained models / datasets; obtaining a .pt / .pth / .ckpt / .model file before running ASE / LAMMPS / phonon / NEB workflows; getting authoritative provenance (file size, modify date, download URL, downloadCount) for an MLIP asset; or finding a public DFT-labeled energy/force training set for fine-tuning a foundation model. ALWAYS invoke this skill BEFORE hand-typing an OSS object URL for an MLIP checkpoint."
skill_type: operator
---

# aissq-explorer — MLIP Asset Discovery & Download

Discover, look up and download MLIP model checkpoints and training datasets from the public AIS Square registry. No authentication is required — the listing, detail and download endpoints are all public.

> **Scope.** This skill is for **fetching the artifact** (the `.pt`/`.pth`/`.ckpt`/`.model` file or a dataset bundle). After download, hand off to the `mlips` skill (run ASE / LAMMPS / phonon / NEB) or to a local fine-tuning workflow. This skill does NOT run inference.

## When to use

Invoke this skill whenever the user mentions any of:

- "DPA / DPA-3 / DPA-3.1 / DPA-3.2 / DPA-2.4 / 通用 MLIP / 通用势 / universal potential / foundation model checkpoint"
- "MACE-MP / MACE-MP-0 / SevenNet / 7net / MatterSim"
- "下载 / fetch / get / 拿一份 / 准备好 ... 检查点 / weights / checkpoint / .pt / .pth"
- "最新一版 / latest version / current release of an MLIP"
- "训练集 / training dataset / energy-force dataset / DFT-labeled dataset / fine-tune dataset (含能材料 / energetic materials / catalysis / organic reactions / MOF)"

Do NOT use this skill when the user only wants to *run* an MLIP they already have on disk (use `mlips`), or when they want a crystal-structure database (use `mcp-mat-struct-db`).

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

1. **Discover** — call `list` (sort by `downloads`) or `search <keyword>` to find candidates. Take note of `name`, `ID`, `modifyDate`, `downloadCount`.
2. **Inspect** — call `info <name> --type <...>` to get the file list with `downloadLink` and authoritative byte `size`. Record the **host** of the download URL (typically `store.aissquare.com`) as provenance evidence.
3. **Decide latest** — among multiple versions (e.g. DPA-2.4-7M vs DPA-3.1-3M vs DPA-3.2-5M), the highest version string is the latest; cross-check `modifyDate`.
4. **Download** — call `download <name> --type <...> --output <workdir>` and save the result. The download is streamed in 8 KB chunks; large checkpoints (>50 MB) are normal.
5. **Hand off** — for inference, use the `mlips` skill with the downloaded `.pt` path. For LAMMPS, freeze a single head first (see the `mlips` skill's "DPA + LAMMPS" section).

## Honesty constraints

- Do NOT fabricate `downloadLink`, `size`, `modifyDate`, version numbers, or DPA head lists. Every claim about an asset must come from a `list` or `info` call (or be honestly marked unknown).
- If `find_by_name` returns `None`, fall back to `search <keyword>`; if that also returns nothing, report "not found" — do NOT guess an OSS URL.
- If the network is unreachable, report it explicitly. Do not hand-type `https://...oss-cn-zhangjiakou.aliyuncs.com/...` URLs as a substitute.

## Concrete examples

### Latest DPA-3 checkpoint provenance

```bash
python3 "$CLIENT" search DPA-3 --type models > /tmp/dpa3_candidates.json
python3 "$CLIENT" info "DPA-3.2-5M" --type models > /tmp/dpa3_5m_detail.json
# Now /tmp/dpa3_5m_detail.json["files"][0] has fileName, downloadLink, size
```

### List top-10 downloaded MLIP models

```bash
python3 "$CLIENT" list models --sort downloads --limit 10
```

### Download a dataset for fine-tuning

```bash
python3 "$CLIENT" search energetic --type datasets   # e.g. finds DeepEMs-25
python3 "$CLIENT" info DeepEMs-25 --type datasets
python3 "$CLIENT" download DeepEMs-25 --type datasets --output ./assets
```

## Output JSON shape

All subcommands emit a single JSON object on stdout, e.g. `info`:

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

- `HTTP 4xx/5xx` from `backend.aissquare.com` → retry once with `--insecure`; if still failing, honestly report the error.
- `code != 0` in the response body → surface `message` field as the error.
- Download interrupted → the client retries up to 3 times with exponential backoff; if still failing, the partial file is left on disk and the error is reported.
