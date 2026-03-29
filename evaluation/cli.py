"""Shim for ``python -m evaluation.cli``; implementation lives in ``evaluation.core.cli``."""

from evaluation.core.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
