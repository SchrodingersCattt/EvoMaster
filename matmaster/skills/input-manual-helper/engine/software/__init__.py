"""Lazy exports for input-manual-helper software backends."""

from __future__ import annotations

from engine.software.base import SoftwareBackend

__all__ = [
    "SoftwareBackend",
    "CP2KBackend",
    "ORCABackend",
    "QEBackend",
    "ABINITBackend",
    "LAMMPSBackend",
    "GROMACSBackend",
    "AbacusBackend",
]


def __getattr__(name: str):
    if name == "AbacusBackend":
        from engine.software.abacus import AbacusBackend

        return AbacusBackend
    if name == "ABINITBackend":
        from engine.software.abinit import ABINITBackend

        return ABINITBackend
    if name == "CP2KBackend":
        from engine.software.cp2k import CP2KBackend

        return CP2KBackend
    if name == "LAMMPSBackend":
        from engine.software.lammps import LAMMPSBackend

        return LAMMPSBackend
    if name == "GROMACSBackend":
        from engine.software.gromacs import GROMACSBackend

        return GROMACSBackend
    if name == "ORCABackend":
        from engine.software.orca import ORCABackend

        return ORCABackend
    if name == "QEBackend":
        from engine.software.qe import QEBackend

        return QEBackend
    raise AttributeError(name)
