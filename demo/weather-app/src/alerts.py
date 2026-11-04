"""Threshold alerts evaluated on a weather payload."""

HIGH_WIND_MPH = 30
HEAT_F = 95
FREEZE_F = 32


def evaluate(data: dict) -> list[str]:
    """Return alert codes for a payload with temperature and wind fields."""
    alerts = []
    if data["wind_mph"] >= HIGH_WIND_MPH:
        alerts.append("HIGH_WIND")
    if data["temp"] >= HEAT_F:
        alerts.append("HEAT")
    if data["temp"] <= FREEZE_F:
        alerts.append("FREEZE")
    return alerts
