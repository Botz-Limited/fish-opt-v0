"""Faza 0: wersja SOFA + lista dokumentów Onshape.

Uruchom: source env.sh && python scripts/00_check_env.py
"""

import logging

import Sofa

from pipeline.onshape_client import OnshapeClient, OnshapeError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main():
    print(f"SOFA: {Sofa.__file__}")
    print(f"SOFA version: {Sofa.GetVersion()}")

    try:
        client = OnshapeClient()
        docs = client.list_documents()
    except OnshapeError as e:
        print(f"Onshape: BŁĄD: {e}")
        return 1

    print(f"Onshape: {len(docs)} dokumentów")
    for d in docs:
        print(f"  {d['name']}  (id={d['id']})")
    print(f"Wywołań API: {client.calls}, X-Rate-Limit-Remaining: {client.rate_limit_remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
