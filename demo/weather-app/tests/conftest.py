import os

import pytest

from src.client import WeatherClient


@pytest.fixture
def client():
    # Tests always talk to whatever API lives at BASE_URL (the mock server in the sandbox).
    return WeatherClient(base_url=os.environ.get("BASE_URL", "http://127.0.0.1:8088"))
