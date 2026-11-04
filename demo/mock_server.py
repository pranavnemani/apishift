"""Deterministic WeatherAPI mock serving either the v1 or the v2 contract.

Stdlib only, so it runs inside the sandbox image with no extra dependencies.

    python mock_server.py --version v2 --port 8088
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CITIES = {
    "Austin": {"temp": 72, "wind_mph": 12},
    "Chicago": {"temp": 50, "wind_mph": 35},
    "Phoenix": {"temp": 104, "wind_mph": 8},
    "Fargo": {"temp": 20, "wind_mph": 18},
    "Boston": {"temp": 30, "wind_mph": 40},
}


def v1_payload(city: str) -> dict:
    c = CITIES[city]
    return {"city": city, "temp": c["temp"], "wind_mph": c["wind_mph"]}


def v2_payload(city: str) -> dict:
    c = CITIES[city]
    return {
        "location": city,
        "temperature": {"current": c["temp"], "unit": "F"},
        "wind": {"speed_mph": c["wind_mph"]},
    }


def make_handler(version: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output clean
            pass

        def _send(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/health":
                return self._send(200, {"ok": True, "version": version})
            if version == "v1" and url.path == "/v1/weather":
                city = q.get("city")
                if city not in CITIES:
                    return self._send(404, {"error": "not_found"})
                return self._send(200, v1_payload(city))
            if version == "v2" and url.path == "/v2/current":
                if self.headers.get("X-API-Version") != "2":
                    return self._send(400, {"error": "missing_version_header"})
                loc = q.get("location")
                if loc is None:
                    return self._send(400, {"error": "missing_location"})
                if loc not in CITIES:
                    return self._send(404, {"error": "not_found"})
                return self._send(200, v2_payload(loc))
            return self._send(404, {"detail": "Not Found"})

    return Handler


def serve(version: str, port: int):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(version))
    server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--port", type=int, default=8088)
    a = ap.parse_args()
    serve(a.version, a.port)
