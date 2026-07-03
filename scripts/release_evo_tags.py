"""Create release tags for matmaster-evo services.

Default mode is dry-run. Use --execute to create local tags, and --push to push
all tags after creation.

Examples:
    uv run python scripts/release_evo_tags.py
    uv run python scripts/release_evo_tags.py --execute --push
    uv run python scripts/release_evo_tags.py --version 0.1.1 --timestamp 2026-07-03-12-45
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"

SERVICES = (
    ("api", "matmaster-evo"),
    ("worker", "matmaster-evo-worker"),
    ("monitor", "matmaster-monitor"),
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_git(args: Sequence[str], *, check: bool = True) -> CommandResult:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    command_result = CommandResult(
        returncode=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit={result.returncode}: "
            f"{command_result.stderr or command_result.stdout}"
        )
    return command_result


def read_project_version() -> str:
    with PYPROJECT_PATH.open("rb") as file:
        data = tomllib.load(file)
    version = (data.get("project") or {}).get("version")
    if not version:
        raise RuntimeError(f"project.version not found in {PYPROJECT_PATH}")
    return str(version)


def default_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H-%M")


def build_tag(service_name: str, version: str, timestamp: str) -> str:
    return f"b_{service_name}_{version}_{timestamp}"


def tag_exists_locally(tag: str) -> bool:
    result = run_git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], check=False)
    return result.returncode == 0


def tag_exists_remotely(tag: str, remote: str) -> bool:
    result = run_git(["ls-remote", "--exit-code", "--tags", remote, tag], check=False)
    return result.returncode == 0


def ensure_clean_worktree() -> None:
    status = run_git(["status", "--porcelain"]).stdout
    if status:
        raise RuntimeError(
            "working tree is not clean; commit or stash changes before creating release tags"
        )


def ensure_tags_available(tags: Sequence[str], remote: str) -> None:
    conflicts = []
    for tag in tags:
        if tag_exists_locally(tag):
            conflicts.append(f"{tag} (local)")
        if tag_exists_remotely(tag, remote):
            conflicts.append(f"{tag} ({remote})")
    if conflicts:
        raise RuntimeError("tag already exists: " + ", ".join(conflicts))


def create_tags(tags: Sequence[str], commit: str) -> None:
    for tag in tags:
        run_git(["tag", tag, commit])


def push_tags(tags: Sequence[str], remote: str) -> None:
    run_git(["push", remote, *tags])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create release tags for matmaster-evo, worker, and monitor."
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Release version. Defaults to project.version in pyproject.toml.",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Release timestamp suffix, e.g. 2026-07-03-12-45. Defaults to now.",
    )
    parser.add_argument(
        "--commit",
        default="HEAD",
        help="Commit to tag. Defaults to HEAD.",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Remote to check and push tags. Defaults to origin.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create local tags. Without this flag, only prints the plan.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push all tags after creating them. Requires --execute.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow creating tags when the working tree is dirty.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.push and not args.execute:
        raise RuntimeError("--push requires --execute")

    version = args.version or read_project_version()
    timestamp = args.timestamp or default_timestamp()
    commit = run_git(["rev-parse", args.commit]).stdout
    tags_by_label = [
        (label, build_tag(service_name, version, timestamp))
        for label, service_name in SERVICES
    ]
    tags = [tag for _, tag in tags_by_label]

    if not args.allow_dirty:
        ensure_clean_worktree()
    ensure_tags_available(tags, args.remote)

    print("Release tag plan:")
    print(f"  commit: {commit}")
    for label, tag in tags_by_label:
        print(f"  {label:<7}: {tag}")
    print(f"  remote: {args.remote}")

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to create tags.")
        return 0

    create_tags(tags, commit)
    print("\nCreated local tags.")
    if args.push:
        push_tags(tags, args.remote)
        print("Pushed tags.")
    else:
        print("Tags were not pushed. Re-run with --execute --push to push them.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Exception
    ) as exc:  # noqa: BLE001 - CLI should print concise operational errors
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
