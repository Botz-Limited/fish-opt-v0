"""Faza 1: dwa zestawy parametrów -> dwa STL, bounding box zgodny z wymiarami.

Uruchom: source env.sh && python scripts/01_export_two.py
"""

import logging
import os
from pathlib import Path

import trimesh

from pipeline.onshape_client import OnshapeClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

B_MM = 10.0  # szerokość belki, stała w modelu
CASES = [{"L": 80.0, "h": 6.0}, {"L": 110.0, "h": 10.0}]
TOL_MM = 0.1


def main():
    client = OnshapeClient()
    did, wid, eid = (os.environ[k] for k in ("ONSHAPE_DID", "ONSHAPE_WID", "ONSHAPE_EID"))
    ok = True
    for case in CASES:
        params = {k: f"{v} mm" for k, v in case.items()}
        out = Path("runs/phase1") / f"beam_L{case['L']:g}_h{case['h']:g}.stl"
        client.export_stl(did, wid, eid, params, out)
        mesh = trimesh.load_mesh(out)
        extents = sorted(mesh.bounding_box.extents)
        expected = sorted([case["L"], B_MM, case["h"]])
        err = max(abs(a - b) for a, b in zip(extents, expected))
        status = "OK" if err < TOL_MM else "ZLE"
        ok &= err < TOL_MM
        print(f"{status} {out.name}: bbox={[round(x, 3) for x in mesh.bounding_box.extents]} mm, "
              f"oczekiwane (posortowane) {expected}, max blad {err:.4f} mm, "
              f"trojkatow {len(mesh.faces)}, watertight={mesh.is_watertight}, min={mesh.bounds[0].round(3)}")
    print(f"Wywolan API: {client.calls}, X-Rate-Limit-Remaining: {client.rate_limit_remaining}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
