"""Faza 3: walidacja SOFA na belce wspornikowej względem wzoru analitycznego, z panelem na żywo.

Część A: zbieżność siatki (method=small, mała siła) vs Timoshenko.
Część B: rampa siły do dużych ugięć, method=small vs large (korotacyjny) vs teoria liniowa.

Uruchom: source env.sh && python scripts/03_validate_beam.py [--no-gui] [--monitor +X+Y]
Wyniki: runs/phase3/validation.json, runs/phase3/validation.png
"""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import trimesh

from pipeline.mesher import make_tet_mesh
from pipeline.sofa_sim import Material, cantilever_analytic, run_sim

OUT = Path("runs/phase3")
L_MM, B_MM, H_MM = 80.0, 10.0, 6.0
SIZES_MM = [2.0, 1.5, 1.0, 0.75, 0.5]
RAMP_SIZE_MM = 1.0
RAMP_STEPS = 20
ACCEPT_ERR = 0.05


class Panel:
    def __init__(self, enabled, geometry):
        self.enabled = enabled
        if not enabled:
            matplotlib.use("Agg")
        else:
            matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        self.plt = plt
        if enabled:
            plt.ion()
        self.fig, axes = plt.subplots(2, 2, figsize=(16, 9))
        self.ax_shape, self.ax_conv, self.ax_ramp, self.ax_text = axes.ravel()
        self.ax_text.axis("off")
        self.fig.suptitle("Faza 3: walidacja belki wspornikowej (SOFA vs teoria)", fontsize=14)
        self.fig.tight_layout(rect=[0, 0, 1, 0.95])
        if enabled:
            try:
                self.fig.canvas.manager.window.wm_geometry(geometry)
            except Exception:
                pass
        self.refresh()

    def refresh(self):
        if self.enabled:
            self.fig.canvas.draw_idle()
            self.plt.pause(0.01)

    def shape(self, rest, pos, title, scale):
        ax = self.ax_shape
        ax.clear()
        disp = np.linalg.norm(pos - rest, axis=1)
        shown = rest + (pos - rest) * scale
        ax.scatter(shown[:, 0], shown[:, 2], c=disp, s=1, cmap="viridis")
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]")
        ax.set_ylabel("z [mm]")
        ax.set_title(title + (f"  (przemieszczenia x{scale:g})" if scale != 1 else ""))
        self.refresh()

    def conv(self, rows, target):
        ax = self.ax_conv
        ax.clear()
        per_h = [H_MM / r["size_mm"] for r in rows]
        err = [100 * (r["tip_disp_mm"] / target - 1) for r in rows]
        ax.plot(per_h, err, "o-", label=f"SOFA P1 tetra, nu={Material().poisson_ratio}")
        ax.axhspan(-100 * ACCEPT_ERR, 100 * ACCEPT_ERR, color="green", alpha=0.15, label="+-5%")
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xlabel("elementy na grubość h")
        ax.set_ylabel("błąd vs Timoshenko [%]")
        ax.set_title("Zbieżność siatki (mała siła, method=small)")
        ax.legend(loc="lower right")
        self.refresh()

    def ramp(self, curves, F_max, analytic_per_N):
        ax = self.ax_ramp
        ax.clear()
        F = np.linspace(0, F_max, 50)
        ax.plot(F, analytic_per_N * F * 1e3, "k--", label="teoria liniowa (Timoshenko)")
        for label, hist in curves.items():
            if hist:
                f, d = zip(*hist)
                ax.plot(f, d, "o-", ms=3, label=label)
        ax.set_xlabel("siła na końcu [N]")
        ax.set_ylabel("ugięcie końca [mm]")
        ax.set_title("Rampa siły: małe vs duże ugięcia")
        ax.legend(loc="upper left")
        self.refresh()

    def text(self, lines):
        self.ax_text.clear()
        self.ax_text.axis("off")
        self.ax_text.text(0.0, 1.0, "\n".join(lines), va="top", family="monospace", fontsize=10)
        self.refresh()


def richardson(rows, n=4):
    """Ekstrapolacja do siatki nieskończenie gęstej: dopasowanie f(h) = f_inf - c*h^p do n najgęstszych siatek."""
    from scipy.optimize import curve_fit
    h = np.array([r["size_mm"] for r in rows[-n:]])
    f = np.array([r["tip_disp_mm"] for r in rows[-n:]])
    try:
        (f_inf, _, p), _ = curve_fit(lambda x, a, c, p: a - c * x**p, h, f, p0=[f[-1], 0.1, 1.0], maxfev=20000)
    except RuntimeError:
        return None, None
    return float(f_inf), float(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--monitor", default="+1997+0", help="pozycja okna X11, domyślnie DP-8")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    panel = Panel(not args.no_gui, f"2560x1380{args.monitor}")

    mat = Material()
    L, b, h = L_MM * 1e-3, B_MM * 1e-3, H_MM * 1e-3
    _, tim_per_N = cantilever_analytic(L, b, h, mat.young_modulus, mat.poisson_ratio, 1.0)
    F_small = 0.005 * L / tim_per_N  # ugięcie ~0.5% L: teoria liniowa w pełni ważna
    target = tim_per_N * F_small * 1e3

    box = OUT / "box.stl"
    trimesh.creation.box(extents=[L_MM, B_MM, H_MM]).export(box)
    log = [f"Belka {L_MM:g} x {B_MM:g} x {H_MM:g} mm, E={mat.young_modulus/1e9:g} GPa, nu={mat.poisson_ratio}",
           f"Siła mała: {F_small:.3f} N -> Timoshenko {target:.4f} mm", ""]
    panel.text(log)

    # Część A: zbieżność siatki
    rows = []
    for size in SIZES_MM:
        msh = make_tet_mesh(box, OUT / f"box_{size:g}.msh", size_min=size / 2, size_max=size)
        res = run_sim(msh, mat, load_N=F_small, method="small",
                      on_step=lambda s, size=size: panel.shape(
                          s["rest"], s["positions"], f"Siatka {size:g} mm", scale=0.1 * L_MM / max(s["tip_disp"], 1e-12)))
        res["size_mm"] = size
        rows.append(res)
        err = res["tip_disp_mm"] / target - 1
        log.append(f"siatka {size:4g} mm  tetra {res['n_tets']:7d}  ugiecie {res['tip_disp_mm']:.4f} mm  blad {100*err:+6.1f}%")
        panel.conv(rows, target)
        panel.text(log)

    f_inf, p = richardson(rows)
    best_err = rows[-1]["tip_disp_mm"] / target - 1
    extrap_err = (f_inf / target - 1) if f_inf else None
    log += ["", f"najgestsza siatka: blad {100*best_err:+.1f}%"]
    if f_inf:
        log.append(f"Richardson (rzad p={p:.2f}): {f_inf:.4f} mm, blad {100*extrap_err:+.1f}%")
    passed = extrap_err is not None and abs(extrap_err) < ACCEPT_ERR
    log.append(f"AKCEPTACJA (|blad ekstrapolacji| < 5%): {'TAK' if passed else 'NIE'}")
    panel.text(log)

    # Część B: rampa siły do ~30% L, small vs large
    F_big = 0.30 * L / tim_per_N
    msh = OUT / f"box_{RAMP_SIZE_MM:g}.msh"
    curves = {"SOFA method=small": [], "SOFA method=large": []}
    finals = {}
    for method in ("small", "large"):
        key = f"SOFA method={method}"

        def on_step(s, key=key, method=method):
            curves[key].append((s["load"], s["tip_disp"]))
            panel.ramp(curves, F_big, tim_per_N)
            panel.shape(s["rest"], s["positions"], f"Rampa {method}: F={s['load']:.0f} N", scale=1)

        finals[method] = run_sim(msh, mat, load_N=F_big, method=method, load_steps=RAMP_STEPS, on_step=on_step)
    lin = tim_per_N * F_big * 1e3
    log += ["", f"Rampa do F={F_big:.0f} N (teoria liniowa: {lin:.1f} mm = 30% L):",
            f"  small: {finals['small']['tip_disp_mm']:.1f} mm, large: {finals['large']['tip_disp_mm']:.1f} mm",
            f"  Newton zbiezny we wszystkich krokach: small={finals['small']['converged']}, large={finals['large']['converged']}",
            "  large < small: belka się obraca i ramię siły maleje (nieliniowość geometryczna).",
            f"  Note: beam theory gives sigma = {6*F_big*L/(b*h**2)/1e6:.0f} MPa >> Rp0.2 = {mat.yield_strength/1e6:.0f} MPa;",
            "  aluminium would yield; this is only a numerical solver check."]
    panel.text(log)

    panel.fig.savefig(OUT / "validation.png", dpi=110)
    summary = {
        "target_mm": target, "F_small_N": F_small, "passed": passed,
        "richardson_mm": f_inf, "richardson_order": p,
        "convergence": [{k: r[k] for k in ("size_mm", "n_tets", "tip_disp_mm", "mass_kg")} for r in rows],
        "ramp": {m: finals[m]["history"] for m in finals},
    }
    (OUT / "validation.json").write_text(json.dumps(summary, indent=2))
    print("\n".join(log))
    print(f"\nZapisano {OUT/'validation.png'} i {OUT/'validation.json'}")
    if panel.enabled:
        panel.plt.ioff()
        panel.plt.show()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
