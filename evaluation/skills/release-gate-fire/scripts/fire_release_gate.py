"""Fire release-gate cases to a live matmaster-evo environment.

Each case is POSTed to the stream API as an independent session.
The worker runs independently — we close the connection after confirming HTTP 200.

Usage:
    uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py --env test --user-id 110680
    uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py --env uat --cases rg_01,rg_05
    uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py --env test --directory /share/eval/my_run
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CASES_PATH = PROJECT_ROOT / "evaluation" / "release_gate" / "cases.yaml"

ENV_URLS = {
    "test": "https://matmaster.test.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat",
    "uat": "https://matmaster.uat.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat",
    "prod": "https://matmaster.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat",
}


def load_cases(filter_ids: list[str] | None = None) -> list[dict]:
    with open(CASES_PATH) as f:
        data = yaml.safe_load(f)
    cases = data.get("cases", [])
    if filter_ids:
        cases = [c for c in cases if c["id"] in filter_ids]
    return cases


def make_session_id(case_id: str, env: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    return f"rg-{case_id}-{env}-{ts}-{short_uuid}"


def fire_case(
    base_url: str,
    session_id: str,
    prompt: str,
    user_id: str,
    directory: str,
    mode: str = "direct",
) -> dict:
    url = f"{base_url}/sessions/{session_id}/stream"
    headers = {
        "X-User-Id": str(user_id),
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {
        "content": prompt,
        "mode": mode,
        "directory": directory,
    }

    resp = requests.post(url, json=body, headers=headers, stream=True, timeout=30)

    if resp.status_code != 200:
        return {
            "status": "error",
            "http_status": resp.status_code,
            "detail": resp.text[:500],
        }

    # Read until we see the first query event (confirms job accepted), then close
    confirmed = False
    for line in resp.iter_lines(decode_unicode=True):
        if line and ("\"type\": \"query\"" in line or "\"type\":\"query\"" in line):
            confirmed = True
            break
        if line and ("\"type\": \"status\"" in line or "\"type\":\"status\"" in line):
            confirmed = True
            break
    resp.close()

    return {"status": "fired" if confirmed else "fired_unconfirmed"}


def main():
    parser = argparse.ArgumentParser(description="Fire release-gate cases to live env")
    parser.add_argument(
        "--env",
        choices=["test", "uat", "prod"],
        default="test",
        help="Target environment (default: test)",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Bohrium user ID (default: from .env.test BOHRIUM_USER_ID)",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Session grouping directory (default: /share/eval/release_gate_<timestamp>)",
    )
    parser.add_argument(
        "--cases",
        default="all",
        help="Comma-separated case IDs or 'all' (default: all)",
    )
    parser.add_argument(
        "--mode",
        default="direct",
        choices=["direct", "planner"],
        help="Agent mode (default: direct)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of cases to fire concurrently (default: 1, sequential)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between sequential fires in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fired without actually sending",
    )

    args = parser.parse_args()

    # Resolve user_id
    user_id = args.user_id
    if not user_id:
        env_file = PROJECT_ROOT / ".env.test"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("BOHRIUM_USER_ID="):
                    user_id = line.split("=", 1)[1].strip()
                    break
    if not user_id:
        print("ERROR: --user-id required (or set BOHRIUM_USER_ID in .env.test)")
        sys.exit(1)

    # Resolve directory
    directory = args.directory
    if not directory:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        directory = f"/share/eval/release_gate_{ts}"

    # Load cases
    filter_ids = None if args.cases == "all" else args.cases.split(",")
    cases = load_cases(filter_ids)
    if not cases:
        print("ERROR: No cases matched filter")
        sys.exit(1)

    base_url = ENV_URLS[args.env]

    # Safety check for prod
    if args.env == "prod" and not args.dry_run:
        confirm = input(
            f"⚠️  About to fire {len(cases)} cases to PROD. Type 'yes' to confirm: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    print(f"Environment: {args.env}")
    print(f"Base URL:    {base_url}")
    print(f"User ID:     {user_id}")
    print(f"Directory:   {directory}")
    print(f"Mode:        {args.mode}")
    print(f"Cases:       {len(cases)}")
    print(f"Parallel:    {args.parallel}")
    print("---")

    if args.dry_run:
        for case in cases:
            sid = make_session_id(case["id"], args.env)
            print(f"[DRY] {case['id']:>6} | {sid} | {case['title']}")
        print(f"\nDry run complete. {len(cases)} cases would be fired.")
        return

    # Fire sequentially (parallel support can be added later with ThreadPoolExecutor)
    results = []
    for i, case in enumerate(cases, 1):
        sid = make_session_id(case["id"], args.env)
        print(f"[{i}/{len(cases)}] {case['id']} → {sid} ... ", end="", flush=True)

        result = fire_case(
            base_url=base_url,
            session_id=sid,
            prompt=case["prompt"],
            user_id=user_id,
            directory=directory,
            mode=args.mode,
        )
        results.append({"case_id": case["id"], "session_id": sid, **result})
        print(result["status"])

        if i < len(cases) and args.delay > 0:
            time.sleep(args.delay)

    # Summary
    print("\n--- Summary ---")
    fired = sum(1 for r in results if r["status"].startswith("fired"))
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"Fired: {fired}/{len(cases)}")
    if errors:
        print(f"Errors: {errors}")
        for r in results:
            if r["status"] == "error":
                print(f"  {r['case_id']}: HTTP {r['http_status']} - {r.get('detail', '')[:100]}")

    print(f"\nFrontend URL:")
    env_domains = {
        "test": "matmaster.test.bohrium.com",
        "uat": "matmaster.uat.bohrium.com",
        "prod": "matmaster.bohrium.com",
    }
    print(f"  https://{env_domains[args.env]}/matmaster/chat-evo/")
    print(f"  Sessions grouped under directory: {directory}")


if __name__ == "__main__":
    main()
