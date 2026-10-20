"""Faza 2: STL z Fazy 1 -> .msh -> wczytanie w SOFA, plus test zepsutego STL.

Uruchom: source env.sh && python scripts/02_mesh.py
"""

from pathlib import Path

import Sofa
import Sofa.Simulation

from pipeline.mesher import MeshingError, make_tet_mesh


def load_in_sofa(msh):
    root = Sofa.Core.Node("root")
    root.addObject("RequiredPlugin", pluginName="Sofa.Component.IO.Mesh")
    root.addObject("RequiredPlugin", pluginName="Sofa.Component.Topology.Container.Constant")
    loader = root.addObject("MeshGmshLoader", name="loader", filename=str(msh.resolve()))
    topo = root.addObject("MeshTopology", src="@loader")
    Sofa.Simulation.init(root)
    return len(loader.position.value), len(topo.tetrahedra.value)


def main():
    stls = sorted(Path("runs/phase1").glob("*.stl"))
    if not stls:
        print("Brak STL w runs/phase1, uruchom najpierw scripts/01_export_two.py")
        return 1
    ok = True
    for stl in stls:
        msh = make_tet_mesh(stl, size_min=1.0, size_max=2.0)
        n_nodes, n_tets = load_in_sofa(msh)
        ok &= n_tets > 0
        print(f"{'OK' if n_tets else 'ZLE'} {msh.name}: wezlow {n_nodes}, tetraedrow {n_tets}")

    broken = Path("runs/phase2/broken.stl")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("solid broken\nendsolid broken\n")
    try:
        make_tet_mesh(broken)
        print("ZLE: zepsuty STL nie rzucil wyjatku")
        ok = False
    except MeshingError as e:
        print(f"OK zepsuty STL -> MeshingError: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
