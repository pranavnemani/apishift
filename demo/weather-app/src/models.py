"""Internal domain model for weather readings.

The rest of the app depends on this shape. It mirrors the v1 upstream
response field-for-field, which is why a v2 upstream change breaks it.
"""
from pydantic import BaseModel, ConfigDict


class WeatherReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    temp: float
    wind_mph: float
