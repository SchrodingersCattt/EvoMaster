# Launching Sandbox contract smoke

This document records the executable contract used before MatMaster enables
Sandbox as the default runtime. Source inspection is complete; live test/uat/prod
results must be recorded separately and must not be inferred from this document.

## Pinned client contract

- Client: `lbg==4.0.0b56`
- E2B dependency supplied by that release: `e2b==2.20.0`
- Template and Sandbox create operations: `lbgcore.sdbx.SdbxOpenApiClient`
- Exec, files, and kill operations: `lbgcore.sdbx.SdbxE2BClient`
- Default SKU under test: `c1_m2_cpu`
- Default test template: `matmaster-test-c1-m2`
- Required mounts: `/personal` and `/share`

The smoke script is
[`scripts/poc_launching_sandbox.py`](../../scripts/poc_launching_sandbox.py).
It imports only the public `lbgcore.sdbx` facade. It does not shell out to the
`lbg` CLI and does not implement a second HTTP client.

## Side-effect-free dry run

Dry run is the default. It does not require `lbg`, credentials, or network
access, and never prints access keys:

```bash
uv run python scripts/poc_launching_sandbox.py --env test
```

The output describes the selected environment, template, SKU, lifecycle, and
checks that a live run would perform.

## Live test against a prebuilt template

Use an isolated test account and project. Do not reuse credentials copied from
design documents, chat logs, shell history, or repository examples.

```bash
export BOHRIUM_ACCESS_KEY='<test-user-access-key>'
export LBG_SDBX_USER_ID='<test-user-id>'
export LBG_SDBX_ORG_ID='<test-org-id>'
export BOHRIUM_PROJECT_ID='<test-project-id>'

uv run --with 'lbg==4.0.0b56' python \
  scripts/poc_launching_sandbox.py \
  --env test \
  --smoke \
  --template-name matmaster-test-c1-m2 \
  --sku-name c1_m2_cpu \
  --confirmed-free-sku c1_m2_cpu
```

`--confirmed-free-sku` is intentionally separate from the live price check.
The public SKU response can prove that the displayed price is `0.00 RMB/h`, but
it cannot expose the deployed `FreeSkuNames` dynamic configuration. A platform
owner must confirm that exact allowlist value before the live command is run.

The script refuses live uat/prod runs unless `--allow-non-test` is also present.
That override permits execution; it does not waive any release gate.

## Disposable Public template validation

To validate owner creation and ordinary-user access separately, provide two
different test access keys. The script creates a unique Public template, runs
the two-Sandbox contract, and deletes the template in `finally`:

```bash
export BOHRIUM_TEMPLATE_ACCESS_KEY='<ci-owner-test-access-key>'
export BOHRIUM_ACCESS_KEY='<ordinary-test-user-access-key>'
export LBG_SDBX_USER_ID='<ordinary-test-user-id>'
export LBG_SDBX_ORG_ID='<ordinary-test-org-id>'
export BOHRIUM_PROJECT_ID='<ordinary-test-project-id>'

uv run --with 'lbg==4.0.0b56' python \
  scripts/poc_launching_sandbox.py \
  --env test \
  --smoke \
  --create-disposable-template \
  --require-distinct-template-owner \
  --image 'registry.dp.tech/.../matmaster:<immutable-version>' \
  --sku-name c1_m2_cpu \
  --confirmed-free-sku c1_m2_cpu
```

The disposable template contract is fixed to:

- `visibility=1` (Public)
- `replicas=0`
- `extra_ephemeral_storage_gb=0`
- `pause_enabled=false`
- exact `sku_name=c1_m2_cpu`

Do not use `--keep-template` in routine smoke runs. It exists only for a
platform owner who needs to inspect a failed disposable template before manual
cleanup.

## Checks performed by the live smoke

The script fails closed unless all of these conditions hold:

1. `/skus` contains the exact configured SKU and reports `0.00 RMB/h`.
2. Template lookup returns an active Public template bound to that SKU.
3. Sandbox create includes a two-hour timeout and
   `bohr.launching.io/mount-user-storage=true`.
4. Both `/personal` and `/share` are writable mount points, not directories on
   the container root filesystem.
5. A text marker in `/personal` survives kill and recreation.
6. A binary payload in `/share` survives recreation byte-for-byte.
7. A marker in `/tmp` does not survive recreation.
8. Every known Sandbox is killed in `finally`, including failure paths.
9. A disposable template created by the run is deleted in `finally`.

The report includes resource IDs and check results, but never access keys.

## Create timeout and orphan handling

Ambiguous Sandbox create failures are deliberately not retried. A 502 or 504
can mean the gateway timed out while the backend continued creating the
resource. Blindly retrying can create multiple orphan Sandboxes. The sole
automatic retry is an explicit HTTP 400 response saying that the template image
cache is not ready: Launching returns that response from its pre-create gate
before forwarding the request to E2B. The script polls exact template state and
retries once only after the cache reaches a terminal Ready/Failed state.

If create returns an ambiguous gateway failure:

1. Stop the smoke run.
2. List recent Sandboxes for the exact template and run ID.
3. Kill every matching resource explicitly.
4. Confirm the deployed orphan-cleanup switch and its observed result.
5. Only then start a new smoke run.

If the script obtained a Sandbox ID before a later check failed, it attempts
cleanup automatically. Any failed cleanup is surfaced as a failed contract,
not hidden as a warning.

## Evidence still required outside the script

The following gates cannot be proven by a client-only test:

- credential rotation and document/repository redaction are complete;
- deployed `FreeSkuNames` contains `c1_m2_cpu`;
- the resulting trade/billing record is zero-cost for the intended identity;
- `max_running_bohr_sandbox_per_user`, TTL, and orphan cleanup match production
  expectations;
- both Open Platform routing cohorts (Launching and standalone bohrsandbox)
  pass the same smoke;
- Node stop/Paused/restart behavior and Paused storage billing are verified by
  the separate Node lifecycle gate.

Record live service version, routing cohort, timestamp, redacted response
fixtures, and billing evidence in the release ticket. Never add access keys,
kubeconfigs, private keys, or bearer tokens to this document.

## Local verification

```bash
uv run pytest tests/scripts/test_poc_launching_sandbox.py -v
uv run python scripts/poc_launching_sandbox.py --env test
```
