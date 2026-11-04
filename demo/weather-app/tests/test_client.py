import pytest

from src.client import CityNotFound
from src.models import WeatherReading


def test_current_returns_reading(client):
    assert isinstance(client.current("Austin"), WeatherReading)


def test_current_temperature(client):
    assert client.current("Austin").temp == 72


def test_current_wind(client):
    assert client.current("Austin").wind_mph == 12


def test_current_city_name(client):
    assert client.current("Austin").city == "Austin"


def test_current_other_city(client):
    reading = client.current("Chicago")
    assert (reading.temp, reading.wind_mph) == (50, 35)


def test_unknown_city_raises(client):
    with pytest.raises(CityNotFound):
        client.current("Atlantis")
