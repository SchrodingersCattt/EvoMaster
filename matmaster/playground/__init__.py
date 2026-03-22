"""Unified Playground -- physical environment preparation layer.

Exposes ``Playground`` as the single public entry point for workspace
creation, session management, cache/logging setup, and immutable
``PlaygroundContext`` output.
"""

from matmaster.playground.playground import Playground

__all__ = ["Playground"]
