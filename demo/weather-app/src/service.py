"""Business logic: build a city weather report with alerts."""
from . import alerts
from .client import WeatherClient
from .models import WeatherReading


def report(city: str, client: WeatherClient | None = None) -> dict:
    client = client or WeatherClient()
    payload = client.fetch_raw(city)
    reading = WeatherReading.model_validate(payload)
    return {
        "city": reading.city,
        "temp": reading.temp,
        "wind_mph": reading.wind_mph,
        "alerts": alerts.evaluate(payload),
    }


def summary_line(city: str, client: WeatherClient | None = None) -> str:
    r = report(city, client)
    flags = ", ".join(r["alerts"]) or "none"
    return f"{r['city']}: {r['temp']:.0f}F, wind {r['wind_mph']:.0f} mph, alerts: {flags}"
