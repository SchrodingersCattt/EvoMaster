from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .version import version_payload


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matmaster-bohrium-transfer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version")
    version_parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        payload = version_payload()
        if args.as_json:
            _print_json(payload)
        else:
            print(
                f"{payload['package']} {payload['package_version']} "
                f"protocol={payload['protocol_version']}"
            )
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
