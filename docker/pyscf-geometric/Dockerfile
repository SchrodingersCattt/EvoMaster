FROM registry.dp.tech/dptech/ubuntu:ubuntu24.04-py3.12

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

RUN python3 -m pip install --upgrade pip setuptools wheel \
    && python3 -m pip install pyscf geometric ase

RUN python3 -c "import geometric, pyscf; from pyscf import gto, scf, dft, mp, cc, tddft; from pyscf.geomopt.geometric_solver import optimize; mol = gto.M(atom='H 0 0 0; H 0 0 0.74', basis='sto-3g', verbose=0); mf = scf.RHF(mol).run(); assert mf.converged; print('pyscf', pyscf.__version__); print('geometric', getattr(geometric, '__version__', 'unknown')); print('H2 RHF energy', mf.e_tot)"
