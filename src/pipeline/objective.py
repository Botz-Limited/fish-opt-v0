"""Funkcja celu: params -> Onshape STL -> siatka -> SOFA -> koszt. Błąd dowolnego kroku = kara.

Cel: maksymalne ugięcie końca przy stałej sile, pod warunkiem von Mises <= stress_limit_MPa.
Koszt = -ugięcie [mm] (minimalizujemy), ograniczenie = vm_max / limit - 1 (<= 0 to wykonalne).

Cache w runs/cache/:
  geom/<hash(params)>/beam.stl         klucz: tylko geometria, oszczędza wywołania API
  sim/<hash(params+SimConfig)>/        klucz: geometria + ustawienia siatki/materiału/obciążenia
"""

import hashlib
import json
import logging
import os
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import trimesh

from pipeline.mesher import make_tet_mesh
from pipeline.sofa_sim import Material, run_sim

log = logging.getLogger(__name__)

CACHE = Path("runs/cache")
PENALTY = 1e6
# Podbij przy każdej zmianie fizyki/solvera w sofa_sim.py: unieważnia stare wyniki w cache.
SIM_VERSION = 4  # 4: material silicone -> aluminium, stresses in MPa
B_MM = 10.0  # szerokość belki, stała w modelu Onshape
# Te same zakresy co w Configuration w Onshape; sprawdzamy przed wywołaniem API, żeby nie palić budżetu.
BOUNDS_MM = {"L": (60.0, 120.0), "h": (4.0, 12.0)}
BBOX_TOL_MM = 0.1


@dataclass(frozen=True)
class SimConfig:
    # Load and limit scaled together from the silicone version (1 mN, 2 kPa) by 5e4: the limit/load ratio is unchanged,
    # so the feasible region in (L, h) is the same. Theory gives 66.7 MPa and ~0.7 mm deflection for L=80, h=6.
    load_N: float = 50.0
    load_steps: int = 5
    # Deflections < 2% of L, so "small" would give the same result; "large" stays as the safe choice for extreme L/h.
    method: str = "large"
    elements_per_thickness: float = 6.0
    # Allowable design stress: Rp0.2 = 276 MPa / safety factor ~2.8.
    stress_limit_MPa: float = 100.0
    stress_exclude_per_h: float = 0.5  # pomijamy elementy bliżej utwierdzenia niż 0.5*h (osobliwość)
    material: Material = field(default_factory=Material)


class GeometryError(RuntimeError):
    pass


def _hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]


def normalize(params):
    """Zaokrąglenie do 0.01 mm, żeby 80.0 i 80.0000001 trafiały w ten sam cache."""
    return {k: round(float(params[k]), 2) for k in sorted(BOUNDS_MM)}


def check_bounds(params):
    for k, (lo, hi) in BOUNDS_MM.items():
        if not lo <= params[k] <= hi:
            raise GeometryError(f"{k}={params[k]} mm poza zakresem [{lo}, {hi}]")


def get_stl(params, client=None):
    """STL z cache albo z Onshape. Zwraca (ścieżka, liczba wywołań API)."""
    folder = CACHE / "geom" / _hash(params)
    stl = folder / "beam.stl"
    if stl.is_file():
        return stl, 0
    if client is None:
        from pipeline.onshape_client import OnshapeClient
        client = OnshapeClient()
    did, wid, eid = (os.environ[k] for k in ("ONSHAPE_DID", "ONSHAPE_WID", "ONSHAPE_EID"))
    calls_before = client.calls
    tmp = folder / "beam.stl.part"
    client.export_stl(did, wid, eid, {k: f"{v} mm" for k, v in params.items()}, tmp)
    check_bbox(tmp, params)
    tmp.rename(stl)  # dopiero po sprawdzeniu bboxa, żeby zły plik nie trafił do cache
    (folder / "params.json").write_text(json.dumps(params))
    return stl, client.calls - calls_before


def check_bbox(stl, params):
    extents = sorted(trimesh.load_mesh(stl, file_type="stl").bounding_box.extents)
    expected = sorted([params["L"], B_MM, params["h"]])
    err = max(abs(a - b) for a, b in zip(extents, expected))
    if err > BBOX_TOL_MM:
        raise GeometryError(f"STL ma wymiary {extents}, oczekiwano {expected} (Onshape przyciął parametr?)")


def cost_from(result, cfg):
    return -result["tip_disp_mm"]


def constraint_from(result, cfg):
    return result["vm_max_MPa"] / cfg.stress_limit_MPa - 1.0


def evaluate_full(params, cfg=SimConfig(), client=None):
    """Zwraca dict: cost, params, tip_disp_mm, mass_kg, api_calls, cached, error, seconds."""
    t0 = time.time()
    params = normalize(params)
    key = _hash({"params": params, "cfg": asdict(cfg), "sim_version": SIM_VERSION})
    folder = CACHE / "sim" / key
    result_file = folder / "result.json"
    if result_file.is_file():
        out = json.loads(result_file.read_text())
        out.update(cached=True, api_calls=0, seconds=time.time() - t0)
        return out

    out = {"params": params, "cost": PENALTY, "constraint": PENALTY, "error": None, "api_calls": 0,
           "cached": False}
    try:
        check_bounds(params)
        stl, out["api_calls"] = get_stl(params, client)
        size = params["h"] / cfg.elements_per_thickness
        msh = make_tet_mesh(stl, folder / "beam.msh", size_min=size / 2, size_max=size)
        res = run_sim(msh, cfg.material, load_N=cfg.load_N, method=cfg.method, load_steps=cfg.load_steps,
                      stress_exclude_mm=cfg.stress_exclude_per_h * params["h"])
        if not res["converged"]:
            raise RuntimeError(f"Newton nie zbiegł: {res['newton_status']}")
        out.update({k: res[k] for k in ("tip_disp_mm", "max_disp_mm", "mass_kg", "n_tets",
                                        "vm_max_MPa", "vm_max_raw_MPa")})
        out["cost"] = cost_from(res, cfg)
        out["constraint"] = constraint_from(res, cfg)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        log.warning("evaluate(%s) -> kara: %s\n%s", params, out["error"], traceback.format_exc(limit=3))
    out["seconds"] = time.time() - t0
    # Zapisujemy też kary za błędy geometrii/siatki: są deterministyczne, powtórka nic nie zmieni.
    # Błędy sieci (OnshapeError) nie trafiają do cache, bo mogą być chwilowe.
    if out["error"] is None or not out["error"].startswith("OnshapeError"):
        folder.mkdir(parents=True, exist_ok=True)
        result_file.write_text(json.dumps(out, indent=2))
    return out


def evaluate(params, cfg=SimConfig(), client=None):
    return evaluate_full(params, cfg, client)["cost"]
