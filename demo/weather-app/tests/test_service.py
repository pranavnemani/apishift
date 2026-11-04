from src import service


def test_report_high_wind(client):
    assert service.report("Chicago", client)["alerts"] == ["HIGH_WIND"]


def test_report_heat(client):
    assert service.report("Phoenix", client)["alerts"] == ["HEAT"]


def test_report_multiple_alerts(client):
    assert service.report("Boston", client)["alerts"] == ["HIGH_WIND", "FREEZE"]


def test_summary_line(client):
    assert service.summary_line("Austin", client) == "Austin: 72F, wind 12 mph, alerts: none"
