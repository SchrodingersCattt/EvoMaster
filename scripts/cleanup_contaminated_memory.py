"""
One-shot cleanup: delete the 5 contaminated memory records from the memory service.

Background
----------
Before the session-isolation fix, all users shared session_id="/app/runs/mat_master_web".
This left 5 orphaned records in the memory service (ChromaDB backend at 101.126.90.82:8002)
that must be manually purged.

After the fix, these records are unreachable by any real user session (each session now gets
a unique UUID as session_id), so they cause no active harm. But they should still be removed
for hygiene.

Pre-requisite
-------------
The memory service must expose POST /api/v1/memory/delete accepting {"ids": [...]}.
This endpoint does NOT exist yet (2026-03-24). Whoever maintains the memory service
(101.126.90.82:8002) must add it first. See note at the bottom of this script.

Usage
-----
    uv run python scripts/cleanup_contaminated_memory.py
    uv run python scripts/cleanup_contaminated_memory.py --url http://101.126.90.82:8002
    uv run python scripts/cleanup_contaminated_memory.py --dry-run   # list only, no delete
"""

import argparse
import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from playground.mat_master.memory.service import memory_delete, memory_list  # noqa: E402

# ---------------------------------------------------------------------------
# The 5 contaminated document IDs (session_id="/app/runs/mat_master_web")
# Confirmed via GET /api/v1/memory/list on 2026-03-24.
# ---------------------------------------------------------------------------
CONTAMINATED_IDS = [
    "8a8e8000-78f0-458d-a987-bc622eb7a872",   # "hello world (with metadata)"
    "a2662236-6786-4b2c-8e93-06644c5089dd",   # DeepEMs-25 article params
    "78856c90-9ced-40f2-ab07-88c0bdf6c82f",   # All 10 MD job IDs
    "f359567f-ae7c-4f16-91ad-efb28ea82540",   # DPA MCP MD results / 2x2x2 progress
    "518fbf1f-c532-404c-85ca-cfe8e15f2f25",   # DPA MCP 2x2x2 supercell NVT-Berendsen job IDs
]

CONTAMINATED_SESSION_ID = "/app/runs/mat_master_web"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete contaminated memory records from the memory service"
    )
    parser.add_argument(
        "--url",
        default="http://101.126.90.82:8002",
        help="Memory service base URL (default: http://101.126.90.82:8002)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List contaminated records only; do not delete",
    )
    parser.add_argument(
        "--verify-after",
        action="store_true",
        default=True,
        help="Re-list all records after deletion to confirm cleanup (default: True)",
    )
    args = parser.parse_args()

    print(f"Memory service: {args.url}")
    print()

    # 1. List current records to confirm contamination
    print("=== Current memory records ===")
    all_docs = await memory_list(base_url=args.url)
    if not all_docs:
        print("No records found. Nothing to clean.")
        return

    contaminated = [d for d in all_docs if d.get("id") in CONTAMINATED_IDS]
    clean = [d for d in all_docs if d.get("id") not in CONTAMINATED_IDS]

    print(f"Total records   : {len(all_docs)}")
    print(f"Contaminated    : {len(contaminated)}  (session_id={CONTAMINATED_SESSION_ID!r})")
    print(f"Clean (keep)    : {len(clean)}")
    print()

    if not contaminated:
        print("No contaminated records found. Already clean.")
        return

    print("Contaminated records to delete:")
    for d in contaminated:
        doc_preview = (d.get("document") or "")[:80]
        if len(d.get("document") or "") > 80:
            doc_preview += "..."
        print(f"  id={d['id']}")
        print(f"     doc={doc_preview!r}")
        meta = d.get("metadata", {})
        if meta:
            print(f"     meta={meta}")
    print()

    if args.dry_run:
        print("[DRY RUN] No records deleted.")
        return

    # 2. Delete contaminated records
    print(f"Deleting {len(contaminated)} contaminated record(s)...")
    ids_to_delete = [d["id"] for d in contaminated]
    result = await memory_delete(ids=ids_to_delete, base_url=args.url)
    print(f"Delete result: {result}")
    print()

    # 3. Verify deletion
    if args.verify_after:
        print("=== Verifying cleanup ===")
        remaining = await memory_list(base_url=args.url)
        still_bad = [d for d in remaining if d.get("id") in CONTAMINATED_IDS]
        if still_bad:
            print(f"WARNING: {len(still_bad)} contaminated record(s) still present!")
            for d in still_bad:
                print(f"  id={d['id']}")
        else:
            print(f"OK: All contaminated records deleted. {len(remaining)} clean record(s) remain.")


if __name__ == "__main__":
    asyncio.run(main())


# ---------------------------------------------------------------------------
# NOTE: Adding the delete endpoint to the memory service
# ---------------------------------------------------------------------------
# The memory service (FastAPI + ChromaDB) at 101.126.90.82:8002 needs:
#
#   @app.post("/api/v1/memory/delete")
#   async def memory_delete_endpoint(body: dict):
#       ids = body.get("ids", [])
#       if not ids:
#           return {"deleted": 0}
#       collection.delete(ids=ids)   # ChromaDB collection.delete(ids=[...])
#       return {"deleted": len(ids)}
#
# After adding this endpoint and redeploying the service, run:
#   uv run python scripts/cleanup_contaminated_memory.py
# ---------------------------------------------------------------------------
