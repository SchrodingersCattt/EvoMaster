"""Optimize a water molecule and write the structure and optimizer log."""

from ase import Atoms
from ase.calculators.emt import EMT
from ase.io import write
from ase.optimize import BFGS

water = Atoms(
    symbols=["O", "H", "H"],
    positions=[
        [0.000, 0.000, 0.000],
        [0.970, 0.000, 0.000],
        [-0.240, 0.930, 0.000],
    ],
)
water.center(vacuum=4.0)
water.calc = EMT()

optimizer = BFGS(water, logfile="b7_log.txt")
optimizer.run(fmax=0.05, steps=100)

energy = water.get_potential_energy()
with open("b7_log.txt", "a", encoding="utf-8") as log_file:
    log_file.write(f"Final energy: {energy:.8f} eV\n")

write("b7_water_optimized.xyz", water)
