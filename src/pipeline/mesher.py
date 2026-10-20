"""STL (powierzchnia) -> siatka tetraedryczna .msh (Gmsh Python API)."""

import math
import tempfile
from pathlib import Path

import gmsh
import trimesh


class MeshingError(RuntimeError):
    pass


def make_tet_mesh(stl_path, out_path=None, size_min=1.0, size_max=3.0, feature_angle_deg=40.0):
    """Zwraca ścieżkę do .msh (format 2.2 ASCII, czytany przez MeshGmshLoader w SOFA).

    size_min / size_max: rozmiar elementu w jednostkach STL (mm).
    Rzuca MeshingError, gdy Gmsh nie zbuduje objętości lub nie wygeneruje tetraedrów.
    """
    stl_path = Path(stl_path)
    out_path = Path(out_path) if out_path else stl_path.with_suffix(".msh")
    if not stl_path.is_file():
        raise MeshingError(f"Brak pliku STL: {stl_path}")

    # Gmsh 4.15 nie czyta binarnych STL ("Error loading"), więc przepisujemy do ASCII przez trimesh.
    try:
        surface = trimesh.load_mesh(stl_path, file_type="stl")
    except Exception as e:
        raise MeshingError(f"Nie da się wczytać STL {stl_path.name}: {e}") from e
    if len(surface.faces) == 0 or not surface.is_watertight:
        raise MeshingError(f"STL {stl_path.name} jest pusty lub niezamknięty (watertight=False)")
    tmpdir = tempfile.TemporaryDirectory()
    ascii_stl = Path(tmpdir.name) / "surface.stl"
    surface.export(ascii_stl, file_type="stl_ascii")

    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMin", size_min)
        gmsh.option.setNumber("Mesh.MeshSizeMax", size_max)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.model.add(stl_path.stem)

        try:
            gmsh.merge(str(ascii_stl))
            # Dzieli trójkąty STL na gładkie płaty po kątach krawędzi, potem buduje z nich geometrię.
            gmsh.model.mesh.classifySurfaces(math.radians(feature_angle_deg), True, True, math.pi)
            gmsh.model.mesh.createGeometry()
            surfaces = [tag for _, tag in gmsh.model.getEntities(2)]
            loop = gmsh.model.geo.addSurfaceLoop(surfaces)
            gmsh.model.geo.addVolume([loop])
            gmsh.model.geo.synchronize()
            gmsh.model.mesh.generate(3)
        except Exception as e:
            raise MeshingError(f"Gmsh nie zbudował siatki dla {stl_path.name}: {e}") from e

        n_tets = sum(len(tags) for etype, tags in zip(*gmsh.model.mesh.getElements(3)[:2]) if etype == 4)
        if n_tets == 0:
            raise MeshingError(f"Brak tetraedrów w siatce dla {stl_path.name} (STL niezamknięty?)")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(out_path))
    finally:
        gmsh.finalize()
        tmpdir.cleanup()
    return out_path
