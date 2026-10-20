"""Faza 4: 5 wywołań evaluate. Sprawdza powtarzalność, cache (0 wywołań API) i karę.

Uruchom: source env.sh && python scripts/04_evaluate_5x.py [--fresh]
--fresh czyści runs/cache przed startem (zużyje ~6 wywołań API).
"""

import argparse
import logging
import shutil

from pipeline.objective import CACHE, PENALTY, evaluate_full
from pipeline.onshape_client import OnshapeClient

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

CALLS = [
    ("nowa geometria", {"L": 80, "h": 6}),
    ("nowa geometria", {"L": 100, "h": 8}),
    ("powtórka #1 (cache)", {"L": 80, "h": 6}),
    ("powtórka #2 (cache, inny zapis liczby)", {"L": 100.0000001, "h": 8.0}),
    ("poza zakresem (kara, 0 API)", {"L": 200, "h": 6}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    if args.fresh and CACHE.exists():
        shutil.rmtree(CACHE)

    client = OnshapeClient()
    results = []
    print(f"{'wywołanie':40s} {'L':>6s} {'h':>5s} {'ugięcie':>9s} {'masa':>7s} {'vM kPa':>7s} {'ogr.':>6s} {'koszt':>9s} {'API':>4s} {'cache':>5s} {'czas':>6s}")
    for label, params in CALLS:
        r = evaluate_full(params, client=client)
        results.append(r)
        tip = f"{r['tip_disp_mm']:.3f}" if "tip_disp_mm" in r else "-"
        mass = f"{r['mass_kg']*1e3:.2f}g" if "mass_kg" in r else "-"
        vm = f"{r['vm_max_kPa']:.3f}" if "vm_max_kPa" in r else "-"
        print(f"{label:40s} {r['params']['L']:6g} {r['params']['h']:5g} {tip:>9s} {mass:>7s} {vm:>7s} {r['constraint']:+6.2f} "
              f"{r['cost']:9.4g} {r['api_calls']:4d} {str(r['cached']):>5s} {r['seconds']:5.1f}s")
        if r["error"]:
            print(f"    błąd: {r['error']}")

    checks = {
        "powtórka #1 == pierwsze wywołanie": results[2]["cost"] == results[0]["cost"],
        "powtórka #2 == drugie wywołanie": results[3]["cost"] == results[1]["cost"],
        "powtórki: 0 wywołań API": results[2]["api_calls"] == 0 and results[3]["api_calls"] == 0,
        "poza zakresem: kara i 0 API": results[4]["cost"] == PENALTY and results[4]["api_calls"] == 0,
        "poprawne geometrie bez błędu": results[0]["error"] is None and results[1]["error"] is None,
    }
    print()
    for name, ok in checks.items():
        print(f"{'OK ' if ok else 'ZLE'} {name}")
    print(f"\nWywołań API w tej sesji: {client.calls}, X-Rate-Limit-Remaining: {client.rate_limit_remaining}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
