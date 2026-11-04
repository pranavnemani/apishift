from src.alerts import evaluate


def test_no_alerts():
    assert evaluate({"temp": 70, "wind_mph": 5}) == []


def test_high_wind():
    assert evaluate({"temp": 70, "wind_mph": 30}) == ["HIGH_WIND"]


def test_heat():
    assert evaluate({"temp": 100, "wind_mph": 5}) == ["HEAT"]


def test_freeze_and_wind():
    assert evaluate({"temp": 20, "wind_mph": 45}) == ["HIGH_WIND", "FREEZE"]
