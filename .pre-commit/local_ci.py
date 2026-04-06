#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

ZERO_SHA = "0" * 40
DEFAULT_BASE_REF = "origin/main"
AUTOFIX_COMMIT_MESSAGE = "style: apply pre-commit autofixes"
MAX_AUTOFIX_COMMITS = 3
REPO_ROOT = Path(__file__).resolve().parents[1]

GitRunner = Callable[[list[str]], str]


class LocalCIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileSnapshot:
    worktree_dirty: frozenset[str]
    index_dirty: frozenset[str]

    @property
    def dirty_files(self) -> frozenset[str]:
        return self.worktree_dirty | self.index_dirty

    @classmethod
    def empty(cls) -> FileSnapshot:
        return cls(worktree_dirty=frozenset(), index_dirty=frozenset())


def parse_name_only_output(output: str) -> list[str]:
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def short_branch_name(remote_ref: str) -> str | None:
    if not remote_ref:
        return None
    if remote_ref.startswith("refs/heads/"):
        return remote_ref.removeprefix("refs/heads/")
    if remote_ref.startswith("refs/"):
        return None
    return remote_ref


def should_run_for_remote_ref(remote_ref: str) -> bool:
    branch_name = short_branch_name(remote_ref)
    return bool(branch_name and branch_name.endswith("test"))


def build_lint_command(files: Sequence[str], all_files: bool) -> list[str]:
    if all_files:
        return ["uv", "run", "pre-commit", "run", "--all-files"]
    return ["uv", "run", "pre-commit", "run", "--files", *files]


def build_test_commands() -> list[list[str]]:
    return [
        ["uv", "run", "--extra", "dev", "python", "-m", "pytest", "tests", "-s"],
        ["uv", "run", "python", "-c", "import src.worker.agent_worker"],
    ]


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise LocalCIError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def collect_tracked_files(*, git_runner: GitRunner = run_git) -> list[str]:
    return parse_name_only_output(git_runner(["ls-files"]))


def resolve_diff_range(
    *,
    base_ref: str | None,
    remote_sha: str | None,
    local_sha: str | None,
    git_runner: GitRunner = run_git,
) -> tuple[str, str]:
    head = local_sha or "HEAD"
    if base_ref:
        return base_ref, head
    if remote_sha and remote_sha != ZERO_SHA:
        return remote_sha, head

    try:
        git_runner(["rev-parse", "--verify", DEFAULT_BASE_REF])
    except LocalCIError as exc:
        raise LocalCIError(
            f"无法解析 {DEFAULT_BASE_REF} 作为本地 CI diff 基线，请先执行: git fetch origin main"
        ) from exc

    base = git_runner(["merge-base", head, DEFAULT_BASE_REF]).strip()
    if not base:
        raise LocalCIError(f"无法基于 {DEFAULT_BASE_REF} 计算 merge-base。")
    return base, head


def collect_changed_files(
    *,
    base_ref: str | None,
    remote_sha: str | None,
    local_sha: str | None,
    git_runner: GitRunner = run_git,
) -> tuple[list[str], tuple[str, str]]:
    base, head = resolve_diff_range(
        base_ref=base_ref,
        remote_sha=remote_sha,
        local_sha=local_sha,
        git_runner=git_runner,
    )
    output = git_runner(["diff", "--name-only", "--diff-filter=d", base, head])
    files = [line.strip() for line in output.splitlines() if line.strip()]
    return files, (base, head)


def capture_file_snapshot(
    files: Sequence[str], *, git_runner: GitRunner = run_git
) -> FileSnapshot:
    if not files:
        return FileSnapshot.empty()

    worktree_dirty = frozenset(
        parse_name_only_output(git_runner(["diff", "--name-only", "--", *files]))
    )
    index_dirty = frozenset(
        parse_name_only_output(
            git_runner(["diff", "--cached", "--name-only", "--", *files])
        )
    )
    return FileSnapshot(worktree_dirty=worktree_dirty, index_dirty=index_dirty)


def detect_autofixed_files(before: FileSnapshot, after: FileSnapshot) -> list[str]:
    return sorted(after.dirty_files - before.dirty_files)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在推送 test 分支前执行本地 lint 和 test 审查。"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--lint-only", action="store_true", help="只运行 lint 检查")
    mode.add_argument("--test-only", action="store_true", help="只运行 test 检查")
    parser.add_argument(
        "--all-files", action="store_true", help="lint 阶段对整个仓库运行 pre-commit"
    )
    parser.add_argument("--base", dest="base_ref", help="手动指定 diff 基线")
    parser.add_argument("--remote-ref", help="pre-push hook 传入的远端 ref")
    parser.add_argument("--remote-sha", help="pre-push hook 传入的远端旧 sha")
    parser.add_argument("--local-sha", help="pre-push hook 传入的本地新 sha")
    return parser.parse_args(argv)


def format_command(command: Sequence[str]) -> str:
    return shlex.join(command)


def run_command(command: Sequence[str]) -> int:
    print(f"执行命令: {format_command(command)}")
    result = subprocess.run(list(command), cwd=REPO_ROOT)
    return result.returncode


def auto_commit_files(
    files: Sequence[str],
    *,
    command_runner: Callable[[Sequence[str]], int] = run_command,
) -> int:
    if not files:
        return 0

    ordered_files = sorted(files)
    add_command = ["git", "add", "--", *ordered_files]
    exit_code = command_runner(add_command)
    if exit_code != 0:
        return exit_code

    commit_command = [
        "git",
        "commit",
        "-m",
        AUTOFIX_COMMIT_MESSAGE,
        "--only",
        "--",
        *ordered_files,
    ]
    return command_runner(commit_command)


def run_lint(args: argparse.Namespace) -> int:
    print("\n[local-ci] 阶段: lint")
    if args.all_files:
        files = collect_tracked_files()
        print(f"lint 目标: 全仓跟踪文件 {len(files)} 个")
    else:
        files, diff_range = collect_changed_files(
            base_ref=args.base_ref,
            remote_sha=args.remote_sha,
            local_sha=args.local_sha,
        )
        print(f"lint diff 范围: {diff_range[0]}..{diff_range[1]}")

    if not files:
        print("lint: 当前 diff 范围内没有变更文件，跳过。")
        return 0

    print(f"lint: 检查 {len(files)} 个变更文件")
    command = build_lint_command(files, all_files=args.all_files)

    for _ in range(MAX_AUTOFIX_COMMITS):
        before = capture_file_snapshot(files)
        exit_code = run_command(command)
        if exit_code == 0:
            return 0

        after = capture_file_snapshot(files)
        autofixed_files = detect_autofixed_files(before, after)
        if not autofixed_files:
            return exit_code

        if before.dirty_files:
            print("lint: 目标文件在 pre-commit 运行前已有未提交改动，拒绝自动提交：")
            for path in sorted(before.dirty_files):
                print(f"  {path}")
            return exit_code

        print(
            f"lint: 检测到 pre-commit 自动改写 {len(autofixed_files)} 个文件，创建独立提交。"
        )
        commit_exit = auto_commit_files(autofixed_files)
        if commit_exit != 0:
            return commit_exit
        print("lint: 已创建 autofix 提交，重新运行 lint 校验。")

    raise LocalCIError("lint 自动修复超过最大重试次数，请手动检查。")


def run_tests() -> int:
    print("\n[local-ci] 阶段: test")
    for command in build_test_commands():
        exit_code = run_command(command)
        if exit_code != 0:
            return exit_code
    return 0


def stage_rerun_hint(stage: str) -> str:
    if stage == "lint":
        return "uv run python .pre-commit/local_ci.py --lint-only"
    return "uv run python .pre-commit/local_ci.py --test-only"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.remote_ref and not should_run_for_remote_ref(args.remote_ref):
        print(f"local-ci: 远端分支 {args.remote_ref} 不匹配 *test，跳过。")
        return 0

    stages: list[tuple[str, Callable[[], int]]] = []
    if not args.test_only:
        stages.append(("lint", lambda: run_lint(args)))
    if not args.lint_only:
        stages.append(("test", run_tests))

    try:
        for stage_name, runner in stages:
            exit_code = runner()
            if exit_code != 0:
                print(f"\nlocal-ci: 阶段 {stage_name} 失败，退出码 {exit_code}")
                print(f"可单独重跑: {stage_rerun_hint(stage_name)}")
                return exit_code
    except LocalCIError as exc:
        print(f"\nlocal-ci: {exc}", file=sys.stderr)
        return 1

    print("\nlocal-ci: 所有检查已通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
