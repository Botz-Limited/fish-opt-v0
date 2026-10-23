"""Belka wspornikowa w SOFA: scena budowana programowo, statyczne obciążanie krokami.

Jednostki w scenie: mm, N, MPa (spójny układ, 1 N/mm^2 = 1 MPa), tak jak siatka .msh.
Metry odpadają, bo GUI SOFA nie wyświetla obiektów rzędu 0.01 jednostki. Material trzyma SI.
Utwierdzenie: ściana x = x_max. Siła: ściana x = x_min, kierunek -z.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import Sofa
import Sofa.Simulation

PLUGINS = [
    "Sofa.Component.AnimationLoop",
    "Sofa.Component.Constraint.Projective",
    "Sofa.Component.Engine.Select",
    "Sofa.Component.IO.Mesh",
    "Sofa.Component.LinearSolver.Direct",
    "Sofa.Component.MechanicalLoad",
    "Sofa.Component.ODESolver.Backward",
    "Sofa.Component.SolidMechanics.FEM.Elastic",
    "Sofa.Component.StateContainer",
    "Sofa.Component.Topology.Container.Constant",
]


@dataclass
class Material:
    # Aluminium EN AW-6061-T6 (catalogue values, not from a material test)
    young_modulus: float = 69e9  # Pa
    poisson_ratio: float = 0.33
    density: float = 2700.0  # kg/m^3
    yield_strength: float = 276e6  # Pa, Rp0.2; the model is linear elastic, results above this are meaningless


class SimulationError(RuntimeError):
    pass


def build_scene(root, mesh_path, material, method="small", newton_iterations=30, face_tol_mm=1e-3):
    for name in PLUGINS:
        root.addObject("RequiredPlugin", pluginName=name)
    root.gravity = [0.0, 0.0, 0.0]
    root.addObject("DefaultAnimationLoop")

    beam = root.addChild("beam")
    # Od SOFA 25.12 parametry Newtona są w osobnym komponencie, StaticSolver znajduje go w węźle.
    # Korotacyjny FEM ma przybliżoną sztywność styczną, więc przy dużych obrotach Newton zbiega liniowo
    # (iloraz ~0.3 na iterację, zmierzone dla L=118, h=4.3), stąd zapas 30 iteracji zamiast 10.
    beam.addObject("NewtonRaphsonSolver", name="newton", maxNbIterationsNewton=newton_iterations,
                   absoluteEstimateDifferenceThreshold=1e-12, absoluteResidualStoppingThreshold=1e-12,
                   maxNbIterationsLineSearch=1, warnWhenLineSearchFails=False)  # 5 iteracji psuło zbieżność sztywnych belek
    beam.addObject("StaticSolver", name="static")
    beam.addObject("SparseLDLSolver", template="CompressedRowSparseMatrixMat3x3d")
    loader = beam.addObject("MeshGmshLoader", name="loader", filename=str(Path(mesh_path).resolve()),
)
    beam.addObject("MeshTopology", name="topo", src="@loader")
    mo = beam.addObject("MechanicalObject", name="dofs", template="Vec3d")
    beam.addObject("TetrahedronFEMForceField", name="fem", method=method,
                   youngModulus=material.young_modulus * 1e-6, poissonRatio=material.poisson_ratio,
                   computeVonMisesStress=2)  # 2 = pełny tensor naprężeń

    # BoxROI potrzebuje bboxa zanim powstanie scena, więc liczymy go z loadera po init.
    loader.init()
    pos = np.asarray(loader.position.value)
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    eps = face_tol_mm
    big = 1.0  # mm zapasu w y i z; duże pudełka psułyby kadr kamery (bbox sceny)
    clamp_box = [hi[0] - eps, lo[1] - big, lo[2] - big, hi[0] + eps, hi[1] + big, hi[2] + big]
    tip_box = [lo[0] - eps, lo[1] - big, lo[2] - big, lo[0] + eps, hi[1] + big, hi[2] + big]
    beam.addObject("BoxROI", name="clampROI", box=clamp_box, drawBoxes=False)
    beam.addObject("FixedProjectiveConstraint", indices="@clampROI.indices")
    beam.addObject("BoxROI", name="tipROI", box=tip_box, drawBoxes=False)
    cff = beam.addObject("ConstantForceField", name="tipLoad", indices="@tipROI.indices",
                         totalForce=[0.0, 0.0, 0.0])
    return {"mo": mo, "loader": loader, "cff": cff, "beam": beam, "bbox": (lo, hi)}


def tet_volume(pos, tets):
    a, b, c, d = (pos[tets[:, i]] for i in range(4))
    return float(np.abs(np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a))).sum() / 6.0)


def run_sim(mesh_path, material=None, load_N=1e-4, method="small", load_steps=1, on_step=None,
            stress_exclude_mm=0.0):
    """Obciąża końcówkę siłą load_N (w -z) w load_steps równych krokach; każdy krok = 1 animate.

    on_step(info) dostaje dict: step, load [N], tip_disp [mm], positions [mm], rest [mm].
    stress_exclude_mm: pomija elementy bliżej utwierdzenia niż ta odległość przy liczeniu vm_max_MPa
    (w narożniku utwierdzenia jest osobliwość naprężeń, surowe maksimum rośnie z gęstością siatki).
    Zwraca dict z wynikami w mm / kg / MPa.
    """
    material = material or Material()
    root = Sofa.Core.Node("root")
    s = build_scene(root, mesh_path, material, method=method)
    Sofa.Simulation.init(root)

    mo, cff = s["mo"], s["cff"]
    tip_idx = np.asarray(root.beam.tipROI.indices.value, dtype=int)
    clamp_idx = np.asarray(root.beam.clampROI.indices.value, dtype=int)
    if len(tip_idx) == 0 or len(clamp_idx) == 0:
        raise SimulationError(f"Pusty BoxROI: tip={len(tip_idx)}, clamp={len(clamp_idx)} węzłów")
    rest = np.array(mo.rest_position.value)
    tets = np.asarray(root.beam.topo.tetrahedra.value, dtype=int)

    newton = root.beam.newton
    # Próg 1e-12 N był nierealny: zbieżne kroki (norma ~1e-9 N przy sile 2e-4 N) dostawały "Diverged".
    # Akceptujemy rezyduum < 0.01% siły jednego kroku.
    newton.absoluteResidualStoppingThreshold = 1e-4 * load_N / load_steps
    history, statuses = [], []
    for k in range(1, load_steps + 1):
        load = load_N * k / load_steps
        cff.totalForce.value = [0.0, 0.0, -load]
        Sofa.Simulation.animate(root, 1.0)
        statuses.append(str(newton.status.value))
        pos = np.array(mo.position.value)
        if not np.all(np.isfinite(pos)):
            raise SimulationError(f"NaN/Inf w położeniach po kroku {k}")
        disp = pos - rest
        tip = float(-disp[tip_idx, 2].mean())
        history.append((load, tip))
        if on_step:
            on_step({"step": k, "load": load, "tip_disp": tip, "positions": pos, "rest": rest,
                     "newton_status": statuses[-1]})

    disp = np.array(mo.position.value) - rest
    vm = np.asarray(root.beam.fem.vonMisesPerElement.value)  # MPa (scene in mm, N)
    dist_to_clamp = rest[:, 0].max() - rest[tets].mean(axis=1)[:, 0]
    vm_far = vm[dist_to_clamp >= stress_exclude_mm]
    volume = tet_volume(rest, tets) * 1e-9  # mm^3 -> m^3
    Sofa.Simulation.unload(root)
    return {
        "tip_disp_mm": history[-1][1],
        "max_disp_mm": float(np.linalg.norm(disp, axis=1).max()),
        "mass_kg": volume * material.density,
        "vm_max_MPa": float(vm_far.max()) if len(vm_far) else float("nan"),
        "vm_max_raw_MPa": float(vm.max()),
        "volume_m3": volume,
        "n_nodes": len(rest),
        "n_tets": len(tets),
        "n_tip_nodes": len(tip_idx),
        "history": history,
        "newton_status": statuses,
        "converged": all("Diverged" not in st for st in statuses),
    }


def cantilever_analytic(L, b, h, E, nu, F):
    """Ugięcie końca belki wspornikowej [m]: Euler-Bernoulli i Timoshenko (z poprawką na ścinanie)."""
    I = b * h**3 / 12.0
    d_eb = F * L**3 / (3 * E * I)
    G = E / (2 * (1 + nu))
    d_shear = F * L / ((5.0 / 6.0) * G * b * h)
    return d_eb, d_eb + d_shear
