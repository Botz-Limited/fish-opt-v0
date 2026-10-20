"""Podgląd belki w GUI SOFA: siła na końcu rośnie co krok (rampa), powierzchnia kolorowana mapą pola.

Uruchom (po source env.sh):
  python scripts/view_beam.py [--field vonmises|displacement] [--method large|small] [--monitor DP-8]
W oknie: czerwona strzałka = siła na końcu, legenda = skala kolorów. Symulacja startuje sama.
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import Sofa
import Sofa.Simulation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pipeline.sofa_sim import Material, build_scene, cantilever_analytic  # noqa: E402

DEFAULTS = {"mesh": "runs/phase3/box_1.msh", "method": "large", "deflection_frac": 0.30, "steps": 40,
            "field": "vonmises"}
FIELD_TITLES = {"vonmises": "von Mises [kPa]", "displacement": "przemieszczenie [mm]"}


class LoadRamp(Sofa.Core.Controller):
    """Podnosi siłę co krok i przelicza pole do pokolorowania powierzchni."""

    def __init__(self, cff, fem, mo, display, colormap, F_max, steps, field, screenshot=None, *a, **kw):
        super().__init__(*a, **kw)
        self.cff, self.fem, self.mo, self.display, self.colormap = cff, fem, mo, display, colormap
        self.F_max, self.steps, self.field, self.k = F_max, steps, field, 0
        self.screenshot, self.frames_after = screenshot, 0

    def onAnimateBeginEvent(self, _):
        if self.k < self.steps:
            self.k += 1
            self.cff.totalForce.value = [0.0, 0.0, -self.F_max * self.k / self.steps]

    def onAnimateEndEvent(self, _):
        if self.field == "vonmises":
            values = np.asarray(self.fem.vonMisesPerNode.value) * 1e3  # MPa -> kPa
        else:
            values = np.linalg.norm(np.asarray(self.mo.position.value) - np.asarray(self.mo.rest_position.value),
                                    axis=1)
        self.display.pointData.value = values.tolist()
        # Legenda nie podąża sama za danymi, więc ustawiamy zakres jawnie (od zera do maksimum).
        self.colormap.min.value = 0.0
        self.colormap.max.value = float(max(values.max(), 1e-12))
        if self.k != getattr(self, "printed", 0):
            self.printed = self.k
            F = self.F_max * self.k / self.steps
            print(f"krok {self.k}/{self.steps}: F = {F*1e3:.3f} mN, max {FIELD_TITLES[self.field]} = {values.max():.3f}")
        if self.screenshot and self.k >= self.steps:
            # kilka klatek po ostatnim kroku, żeby GUI zdążyło narysować stan końcowy
            self.frames_after += 1
            if self.frames_after == 5:
                import Sofa.Gui
                Sofa.Gui.GUIManager.SaveScreenshot(self.screenshot)
            elif self.frames_after > 8:
                os._exit(0)


def boundary_triangles(tets):
    """Ściany występujące w dokładnie jednym tetraedrze = powierzchnia bryły, zorientowane na zewnątrz."""
    tets = np.asarray(tets, dtype=int)
    faces = np.concatenate([tets[:, [0, 2, 1]], tets[:, [0, 1, 3]], tets[:, [0, 3, 2]], tets[:, [1, 2, 3]]])
    _, inverse, counts = np.unique(np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True)
    return faces[counts[inverse.ravel()] == 1].tolist()


def createScene(root, mesh=DEFAULTS["mesh"], method=DEFAULTS["method"],
                deflection_frac=DEFAULTS["deflection_frac"], steps=DEFAULTS["steps"], field=DEFAULTS["field"],
                screenshot=None):
    mat = Material()
    s = build_scene(root, mesh, mat, method=method)
    lo, hi = s["bbox"]
    L, b, h = (hi - lo)  # mm
    _, tim_per_N = cantilever_analytic(L * 1e-3, b * 1e-3, h * 1e-3, mat.young_modulus, mat.poisson_ratio, 1.0)
    F_max = deflection_frac * L * 1e-3 / tim_per_N  # siła dająca ugięcie deflection_frac*L wg teorii liniowej

    for name in ("Sofa.Component.Visual", "Sofa.GL.Component.Rendering3D", "Sofa.GL.Component.Rendering2D",
                 "Sofa.Component.Mapping.Linear", "Sofa.Component.Topology.Container.Dynamic", "Sofa.Component.Setting"):
        root.addObject("RequiredPlugin", pluginName=name)
    root.addObject("DefaultVisualManagerLoop")
    root.addObject("VisualStyle", displayFlags="showVisualModels showForceFields")
    root.addObject("BackgroundSetting", color=[0.15, 0.15, 0.18, 1.0])
    fem = root.beam.fem
    fem.computeVonMisesStress = 2  # pełny tensor naprężeń, nie tylko liniowe przybliżenie
    s["cff"].showArrowSize = 0.25 * L / F_max  # strzałka siły ~ 1/4 długości belki przy pełnym obciążeniu
    s["cff"].showColor = [1.0, 0.2, 0.2, 1.0]

    # Powierzchnia kolorowana polem: tylko zewnętrzne ściany tetraedrów, ruszają się razem z węzłami FEM.
    visu = s["beam"].addChild("visu")
    visu.addObject("TriangleSetTopologyContainer", name="tris", position="@../loader.position",
                   triangles=boundary_triangles(s["loader"].tetrahedra.value))
    colormap = visu.addObject("OglColorMap", name="colormap", colorScheme="Blue to Red", showLegend=True,
                              legendTitle=FIELD_TITLES[field], legendSize=14)
    display = visu.addObject("DataDisplay", name="display", pointData=[0.0] * len(s["loader"].position.value))
    visu.addObject("IdentityMapping")
    # Kadr kamery: bbox korzenia nie liczy się sam z węzłów potomnych, a GUI kadruje według niego.
    # Dokładamy w dół spodziewane ugięcie, żeby końcówka nie wyjechała z kadru.
    root.bbox = [[lo[0], lo[1], lo[2] - deflection_frac * L], hi.tolist()]
    # Kamera z boku (od -y), oś z w górę, żeby ugięcie było widać. Kwaternion [x,y,z,w]: obrót +90° wokół x.
    center = (lo + hi) / 2 - [0.0, 0.0, 0.5 * deflection_frac * L]
    root.addObject("InteractiveCamera", name="camera", position=(center - [0.0, 1.6 * L, 0.0]).tolist(),
                   orientation=[0.7071068, 0.0, 0.0, 0.7071068], lookAt=center.tolist(), distance=1.6 * L)

    root.dt = 1.0
    s["beam"].addObject(LoadRamp(s["cff"], fem, s["mo"], display, colormap, F_max, steps, field, screenshot, name="ramp"))
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", default=DEFAULTS["mesh"])
    ap.add_argument("--method", default=DEFAULTS["method"], choices=["small", "large"])
    ap.add_argument("--field", default=DEFAULTS["field"], choices=list(FIELD_TITLES))
    ap.add_argument("--frac", type=float, default=DEFAULTS["deflection_frac"])
    ap.add_argument("--steps", type=int, default=DEFAULTS["steps"])
    ap.add_argument("--gui", default="imgui", choices=["imgui", "glfw"])
    ap.add_argument("--monitor", default="DP-8", help="nazwa wyjścia KDE (xrandr --listmonitors)")
    ap.add_argument("--screenshot", help="zapisz zrzut po ostatnim kroku i zamknij (do testów)")
    args = ap.parse_args()

    import Sofa.Gui
    import SofaRuntime
    from pipeline.kde_window import place_window_later
    SofaRuntime.importPlugin("SofaImGui" if args.gui == "imgui" else "SofaGLFW")

    root = Sofa.Core.Node("root")
    createScene(root, args.mesh, args.method, args.frac, args.steps, args.field, args.screenshot)
    frame = [list(v) for v in root.bbox.value]
    Sofa.Simulation.init(root)
    root.bbox = frame  # init nadpisuje bbox korzenia, przywracamy kadr z createScene
    Sofa.Gui.GUIManager.Init("beam", args.gui)
    Sofa.Gui.GUIManager.createGUI(root, __file__)
    Sofa.Gui.GUIManager.SetDimension(1600, 1000)
    root.animate = True
    place_window_later(os.getpid(), args.monitor)
    Sofa.Gui.GUIManager.MainLoop(root)
    Sofa.Gui.GUIManager.closeGUI()


if __name__ == "__main__":
    main()
