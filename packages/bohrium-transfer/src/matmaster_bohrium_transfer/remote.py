from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .archive import create_zip_store
from .client import StoreHostClient
from .download import run_download_results_payload
from .manifest import ManifestStore
from .multipart import upload_file_multipart
from .security import redact_secrets
from .version import PROTOCOL_VERSION, SCHEMA_VERSION, version_payload


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _load_payload(path: str) -> dict[str, object]:
    payload_path = Path(path)
    raw = payload_path.read_text(encoding="utf-8")
    payload_path.unlink(missing_ok=True)
    payload = json.loads(raw)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("payload schema_version mismatch")
    return payload


def _upload_submit(payload: dict[str, object]) -> dict[str, object]:
    input_dir = Path(str(payload["input_dir"]))
    store_host = str(payload["store_host"]).rstrip("/")
    store_path = str(payload["store_path"]).strip().rstrip("/") + "/"
    token = str(payload["token"])
    object_name = str(payload.get("object_name") or "input.zip")
    transfer_root = Path(
        str(payload.get("transfer_root") or "/share/.matmaster/transfers")
    )
    archive_path = transfer_root / "archives" / object_name
    archive = create_zip_store(input_dir, archive_path)
    object_key = f"{store_path}{object_name}"
    client = StoreHostClient(store_host, token)
    summary = upload_file_multipart(
        client=client,
        file_path=archive.archive_path,
        object_key=object_key,
        manifest_store=ManifestStore(transfer_root),
        transfer_id=str(
            payload.get("transfer_id") or f"submit-input-{abs(hash(object_key))}"
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "ok": True,
        "oss_key": object_key,
        "bytes_total": summary["bytes_total"],
        "parts_total": summary["parts_total"],
    }


def _download_results(payload: dict[str, object]) -> dict[str, object]:
    return run_download_results_payload(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matmaster-bohrium-transfer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version")
    version_parser.add_argument("--json", action="store_true", dest="as_json")
    upload_parser = subparsers.add_parser("upload-submit")
    upload_parser.add_argument("--payload-file", required=True)
    download_parser = subparsers.add_parser("download-results")
    download_parser.add_argument("--payload-file", required=True)

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
    if args.command == "upload-submit":
        try:
            _print_json(_upload_submit(_load_payload(args.payload_file)))
            return 0
        except Exception as exc:
            _print_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "ok": False,
                    "stage": "upload_submit",
                    "retryable": False,
                    "safe_message": redact_secrets(str(exc)),
                    "resume_available": False,
                }
            )
            return 1
    if args.command == "download-results":
        try:
            _print_json(_download_results(_load_payload(args.payload_file)))
            return 0
        except Exception as exc:
            _print_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "ok": False,
                    "stage": "download_results",
                    "retryable": False,
                    "safe_message": redact_secrets(str(exc)),
                    "resume_available": False,
                }
            )
            return 1
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
