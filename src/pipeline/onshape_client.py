"""Cienki klient REST Onshape (requests + Basic Auth kluczami API).

Każde wywołanie jest liczone i loguje X-Rate-Limit-Remaining, żeby pilnować budżetu API.
"""

import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

API = "/api/v10"


class OnshapeError(RuntimeError):
    pass


class OnshapeClient:
    def __init__(self, access_key=None, secret_key=None, base_url=None):
        load_dotenv()
        self.access_key = access_key or os.environ.get("ONSHAPE_ACCESS_KEY")
        self.secret_key = secret_key or os.environ.get("ONSHAPE_SECRET_KEY")
        self.base_url = (base_url or os.environ.get("ONSHAPE_BASE_URL", "https://cad.onshape.com")).rstrip("/")
        if not self.access_key or not self.secret_key:
            raise OnshapeError("Brak ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY w .env")
        self.session = requests.Session()
        self.session.auth = (self.access_key, self.secret_key)
        self.session.headers["Accept"] = "application/json;charset=UTF-8; qs=0.09"
        self.calls = 0
        self.rate_limit_remaining = None

    def request(self, method, path, max_retries=3, **kwargs):
        url = path if path.startswith("http") else f"{self.base_url}{API}{path}"
        for attempt in range(max_retries + 1):
            self.calls += 1
            resp = self.session.request(method, url, timeout=120, allow_redirects=False, **kwargs)
            if resp.status_code in (301, 302, 303, 307, 308):
                # Eksporty przekierowują na inny host; requests zrzuca wtedy auth, więc idziemy ręcznie.
                location = resp.headers["Location"]
                log.info("%s %s -> %s, przekierowanie na %s", method, path, resp.status_code,
                         location.split("?")[0])
                self.calls += 1
                resp = self.session.request("GET", location, timeout=120, allow_redirects=False,
                                            headers=kwargs.get("headers"))
            remaining = resp.headers.get("X-Rate-Limit-Remaining")
            if remaining is not None:
                self.rate_limit_remaining = remaining
            log.info("%s %s -> %s (call #%d, X-Rate-Limit-Remaining=%s)",
                     method, path, resp.status_code, self.calls, remaining)
            if resp.status_code == 429 and attempt < max_retries:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("429 rate limit, czekam %.1f s", wait)
                time.sleep(wait)
                continue
            if not resp.ok:
                raise OnshapeError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:500]}")
            return resp
        raise OnshapeError(f"{method} {path}: przekroczono liczbę prób po 429")

    def list_documents(self, limit=20):
        resp = self.request("GET", "/documents", params={"limit": limit, "filter": 0})
        return resp.json().get("items", [])

    def encode_configuration(self, did, eid, params):
        """params: {"L": "80 mm", "h": "6 mm"} -> zakodowany string dla parametru `configuration`.

        Wartości to stringi z jednostką, jak w UI Onshape. Nie składamy stringa ręcznie (patrz HANDOFF).
        """
        body = {"parameters": [{"parameterId": k, "parameterValue": str(v)} for k, v in params.items()]}
        resp = self.request("POST", f"/elements/d/{did}/e/{eid}/configurationencodings", json=body)
        # encodedId jest surowy; queryParam jest już URL-encoded i requests zakodowałby go drugi raz.
        return resp.json()["encodedId"]

    def export_stl(self, did, wid, eid, params, out_path, resolution="fine"):
        """Eksport STL Part Studio dla zadanej konfiguracji, jednostki: milimetry."""
        configuration = self.encode_configuration(did, eid, params) if params else ""
        query = {
            "mode": "binary",
            "units": "millimeter",
            "scale": 1.0,
            "grouping": "true",
            "resolution": resolution,
            "configuration": configuration,
        }
        resp = self.request(
            "GET", f"/partstudios/d/{did}/w/{wid}/e/{eid}/stl",
            params=query, headers={"Accept": "application/octet-stream"},
        )
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return out_path
