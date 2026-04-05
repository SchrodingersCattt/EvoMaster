"""Minimal legacy compatibility exports for removed playground.core modules."""

from .step_verifier import StepContract, verify_step_deterministic

__all__ = ["StepContract", "verify_step_deterministic"]
