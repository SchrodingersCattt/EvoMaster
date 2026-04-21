from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        '{"schema_version":"v1","protocol_version":"1.0","ok":false,'
        '"stage":"legacy_helper_removed",'
        '"safe_message":"legacy transfer helper has been removed; '
        'install matmaster_bohrium_transfer on the remote image"}'
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
