from __future__ import annotations

import geometric
import pyscf
from pyscf import cc, dft, gto, mp, scf, tddft
from pyscf.geomopt.geometric_solver import optimize


def main() -> None:
    print(f"PySCF {pyscf.__version__}")
    print(f"geomeTRIC {getattr(geometric, '__version__', 'unknown')}")

    h2 = gto.M(
        atom="H 0 0 0; H 0 0 0.74",
        basis="sto-3g",
        verbose=0,
    )
    rhf = scf.RHF(h2).run()
    assert rhf.converged, "H2 RHF did not converge"
    print(f"H2 RHF energy: {rhf.e_tot:.12f}")

    water = gto.M(
        atom="O 0.000000 0.000000 0.000000; H 0.000000 -0.757000 0.587000; H 0.000000 0.757000 0.587000",
        basis="sto-3g",
        verbose=0,
    )
    rks = dft.RKS(water)
    rks.xc = "b3lyp"
    rks.max_cycle = 50
    rks.kernel()
    assert rks.converged, "H2O B3LYP did not converge"
    opt_mol = optimize(rks, maxsteps=3)
    print(f"Optimized atoms: {opt_mol.natm}")

    # Keep these imports live so the smoke test covers the advertised modules.
    assert mp and cc and tddft
    print("PySCF/geomeTRIC smoke test passed")


if __name__ == "__main__":
    main()
