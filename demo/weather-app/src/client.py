"""HTTP client for the upstream WeatherAPI (currently written against v1)."""
import os

import requests

from .models import WeatherReading


class CityNotFound(Exception):
    """Raised when the upstream API does not know the requested city."""


class WeatherClient:
    def __init__(self, base_url: str | None = None, session: requests.Session | None = None):
        self.base_url = (base_url or os.environ.get("BASE_URL", "http://127.0.0.1:8088")).rstrip("/")
        self.session = session or requests.Session()

    def fetch_raw(self, city: str) -> dict:
        """Return the upstream JSON payload for a city, unmodified."""
        resp = self.session.get(
            f"{self.base_url}/v1/weather",
            params={"city": city},
            timeout=5,
        )
        if resp.status_code == 404 and resp.json().get("error") == "not_found":
            raise CityNotFound(city)
        resp.raise_for_status()
        return resp.json()

    def current(self, city: str) -> WeatherReading:
        return WeatherReading.model_validate(self.fetch_raw(city))
