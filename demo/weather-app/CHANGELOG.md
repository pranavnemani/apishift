# WeatherAPI changelog

## 2.0.0

Breaking changes. v1 endpoints are retired.

- `GET /v1/weather` is replaced by `GET /v2/current`.
- The `city` query parameter is renamed to `location`.
- Every v2 request must send the header `X-API-Version: 2`; requests without it get `400`.
- The response is restructured: `city` becomes `location`, `temp` moves to `temperature.current`,
  and `wind_mph` moves to `wind.speed_mph`. A new `temperature.unit` field is always `"F"`.
- Unknown locations still return `404` with `{"error": "not_found"}`.
