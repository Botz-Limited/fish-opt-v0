"""Faza 5: Optuna (TPE z ograniczeniem) nad evaluate_full. Study w SQLite, panel na żywo, wykres końcowy.

Uruchom: source env.sh && python -m pipeline.optimize [--trials 20] [--sampler gp|tpe] [--warm-start]
                                                     [--no-gui] [--close] [--study beam-al-v1]
Study jest wznawialne: ponowne uruchomienie z tą samą nazwą dokłada próby do istniejących.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import matplotlib
import numpy as np
import optuna

from pipeline.objective import B_MM, BOUNDS_MM, PENALTY, SimConfig, evaluate_full
from pipeline.onshape_client import OnshapeClient

RUNS = Path("runs")
STEP_MM = {"L": 1.0, "h": 0.1}  # dyskretyzacja: powtórki trafiają w cache zamiast w API


class LivePanel:
    def __init__(self, enabled, cfg, monitor):
        self.enabled, self.cfg = enabled, cfg
        matplotlib.use("TkAgg" if enabled else "Agg")
        import matplotlib.pyplot as plt
        self.plt = plt
        if enabled:
            plt.ion()
        self.fig, (self.ax_space, self.ax_hist) = plt.subplots(1, 2, figsize=(16, 8))
        self.fig.suptitle(f"Faza 5: max ugięcie przy von Mises <= {cfg.stress_limit_MPa:g} MPa "
                          f"(F = {cfg.load_N:g} N, aluminium)", fontsize=14)
        if enabled:
            from pipeline.kde_window import place_window_later
            place_window_later(os.getpid(), monitor)

    def update(self, study):
        trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not trials:
            return
        ax = self.ax_space
        ax.clear()
        h = np.linspace(*BOUNDS_MM["h"], 200)
        # Granica wg teorii belek: sigma = 6 F L / (b h^2) = limit  ->  L = limit * b * h^2 / (6 F)
        L_lim = self.cfg.stress_limit_MPa * 1e6 * (B_MM * 1e-3) * (h * 1e-3) ** 2 / (6 * self.cfg.load_N) * 1e3
        ax.plot(h, L_lim, "k--", label="granica naprężeń wg teorii")
        ax.fill_between(h, L_lim, BOUNDS_MM["L"][1] + 10, color="red", alpha=0.07)
        for t in trials:
            ok = t.user_attrs.get("constraint", PENALTY) <= 0
            ax.scatter(t.params["h"], t.params["L"], c="tab:green" if ok else "tab:red", s=50, edgecolors="k")
            ax.annotate(str(t.number), (t.params["h"], t.params["L"]), fontsize=7, xytext=(4, 4),
                        textcoords="offset points")
        best = best_feasible(study)
        if best:
            ax.scatter(best.params["h"], best.params["L"], marker="*", s=400, c="gold", edgecolors="k",
                       zorder=5, label=f"najlepsza #{best.number}: {-best.value:.2f} mm")
        ax.set_xlim(*BOUNDS_MM["h"])
        ax.set_ylim(BOUNDS_MM["L"][0] - 2, BOUNDS_MM["L"][1] + 2)
        ax.set_xlabel("h [mm]")
        ax.set_ylabel("L [mm]")
        ax.set_title("Przestrzeń parametrów (zielone = spełnia limit, czerwone = nie)")
        ax.legend(loc="lower right")

        ax = self.ax_hist
        ax.clear()
        nums = [t.number for t in trials]
        tips = [t.user_attrs.get("tip_disp_mm", np.nan) for t in trials]
        feas = [t.user_attrs.get("constraint", PENALTY) <= 0 for t in trials]
        ax.scatter(nums, tips, c=["tab:green" if f else "tab:red" for f in feas], edgecolors="k", s=40)
        running, best_so_far = -np.inf, []
        for tip, f in zip(tips, feas):
            if f and tip > running:
                running = tip
            best_so_far.append(running if np.isfinite(running) else np.nan)
        ax.step(nums, best_so_far, where="post", color="gold", lw=2, label="najlepsze dopuszczalne")
        ax.set_xlabel("próba")
        ax.set_ylabel("ugięcie końca [mm]")
        ax.set_title("Historia (cel: maksymalizacja)")
        ax.legend(loc="lower right")
        self.fig.tight_layout(rect=[0, 0, 1, 0.94])
        if self.enabled:
            self.fig.canvas.draw_idle()
            self.plt.pause(0.05)


def theory_points(cfg, lengths=(120.0, 110.0, 100.0), margin=0.95):
    """Punkty startowe z teorii belek: dla danego L grubość h, przy której sigma = 6FL/(bh^2) = margin*limit."""
    pts = []
    for L in lengths:
        h_m = np.sqrt(6 * cfg.load_N * L * 1e-3 / (B_MM * 1e-3 * cfg.stress_limit_MPa * 1e6 * margin))
        h = float(np.clip(np.ceil(h_m * 1e3 / STEP_MM["h"]) * STEP_MM["h"], *BOUNDS_MM["h"]))
        pts.append({"L": L, "h": round(h, 1)})
    return pts


def make_sampler(name, seed):
    constraints = lambda t: [t.user_attrs.get("constraint", PENALTY)]  # noqa: E731
    if name == "gp":
        # Optymalizacja bayesowska (proces gaussowski): modeluje i cel, i ograniczenie, więc szuka krawędzi.
        return optuna.samplers.GPSampler(seed=seed, n_startup_trials=6, constraints_func=constraints)
    return optuna.samplers.TPESampler(seed=seed, n_startup_trials=8, constraints_func=constraints)


def best_feasible(study):
    ok = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
          and t.user_attrs.get("constraint", PENALTY) <= 0]
    return min(ok, key=lambda t: t.value) if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--study", default="beam-al-v1")  # beam-v1 = old silicone trials
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--monitor", default="DP-8")
    ap.add_argument("--sampler", default="gp", choices=["gp", "tpe"])
    ap.add_argument("--warm-start", action="store_true", help="zacznij od 3 punktów z teorii belek")
    ap.add_argument("--close", action="store_true", help="zamknij panel po zakończeniu")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    cfg = SimConfig()
    client = OnshapeClient()
    RUNS.mkdir(exist_ok=True)
    sampler = make_sampler(args.sampler, args.seed)
    study = optuna.create_study(study_name=args.study, storage=f"sqlite:///{RUNS}/study.db",
                                sampler=sampler, direction="minimize", load_if_exists=True)
    if args.warm_start and not study.trials:
        for pt in theory_points(cfg):
            study.enqueue_trial(pt)
    panel = LivePanel(not args.no_gui, cfg, args.monitor)
    panel.update(study)

    def objective(trial):
        params = {k: trial.suggest_float(k, *BOUNDS_MM[k], step=STEP_MM[k]) for k in ("L", "h")}
        r = evaluate_full(params, cfg, client)
        for k in ("tip_disp_mm", "vm_max_MPa", "mass_kg", "api_calls", "cached", "error", "constraint"):
            if k in r:
                trial.set_user_attr(k, r[k])
        status = "OK " if r["constraint"] <= 0 else ("BLAD" if r["error"] else "PRZEKR.")
        print(f"#{trial.number:3d} L={params['L']:6.1f} h={params['h']:5.2f}  "
              f"ugięcie={r.get('tip_disp_mm', float('nan')):7.3f} mm  vM={r.get('vm_max_MPa', float('nan')):6.2f} MPa  "
              f"{status:7s} API={r['api_calls']} (razem {client.calls})", flush=True)
        return r["cost"]

    study.optimize(objective, n_trials=args.trials, callbacks=[lambda s, t: panel.update(s)])

    best = best_feasible(study)
    panel.update(study)
    panel.fig.savefig(RUNS / f"{args.study}_history.png", dpi=110)
    summary = {"study": args.study, "sampler": args.sampler, "warm_start": args.warm_start, "n_trials": len(study.trials), "api_calls_this_run": client.calls,
               "best": {"number": best.number, "params": best.params, **best.user_attrs} if best else None}
    (RUNS / f"{args.study}_best.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Zapisano {RUNS/(args.study + '_history.png')}, {RUNS/(args.study + '_best.json')}, "
          f"study w {RUNS/'study.db'}", flush=True)
    if panel.enabled and not args.close:
        panel.plt.ioff()
        panel.plt.show()


if __name__ == "__main__":
    main()
