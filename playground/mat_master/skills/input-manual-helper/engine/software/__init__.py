"""
engine/software 包 — 各软件的 SoftwareBackend 实现。

导出：
  SoftwareBackend — 抽象基类（来自 base.py）
  CP2KBackend     — CP2K 后端骨架（Phase 1 实现）
  ORCABackend     — ORCA 后端骨架（Phase 1 实现）
  QEBackend       — Quantum ESPRESSO 后端骨架（Phase 1 实现）
  ABINITBackend   — ABINIT 后端骨架（Phase 1 实现）
  LAMMPSBackend   — LAMMPS 后端骨架（Phase 1 实现）
"""

from engine.software.base import SoftwareBackend
from engine.software.cp2k import CP2KBackend
from engine.software.orca import ORCABackend
from engine.software.qe import QEBackend
from engine.software.abinit import ABINITBackend
from engine.software.lammps import LAMMPSBackend

__all__ = [
    "SoftwareBackend",
    "CP2KBackend",
    "ORCABackend",
    "QEBackend",
    "ABINITBackend",
    "LAMMPSBackend",
]
