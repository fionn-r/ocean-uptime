"""Stormglass.io marine weather API client."""

from __future__ import annotations

import time
from datetime import datetime
from typing import cast

import requests
from loguru import logger

from good_surf.exceptions import StormglassAPIError
from good_surf.models import WeatherDataPoint

_STORMGLASS_ENDPOINT = "https://api.stormglass.io/v2/weather/point"
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = (5, 15, 30)  # wait before attempt 2, 3, (then give up)


class StormglassClient:
    """Client for the Stormglass.io marine weather API.

    Designed for dependency injection: pass a requests.Session so callers can
    substitute a fake in tests without monkeypatching global state.

    Args:
        api_key: Stormglass API key.
        session: requests.Session used for HTTP calls.
    """

    def __init__(self, api_key: str, session: requests.Session) -> None:
        self._api_key = api_key
        self._session = session

    def fetch_forecast(
        self,
        start: datetime,
        end: datetime,
        lat: float,
        lng: float,
        params: tuple[str, ...],
    ) -> list[WeatherDataPoint]:
        """Fetch hourly marine weather data from Stormglass.

        Args:
            start: Start of the forecast window (UTC-aware).
            end: End of the forecast window (UTC-aware).
            lat: Latitude of the location.
            lng: Longitude of the location.
            params: Tuple of Stormglass parameter names to request.

        Returns:
            List of WeatherDataPoint objects ordered by time.

        Raises:
            StormglassAPIError: On HTTP errors or unexpected response structure.
        """
        query_params = {
            "lat": lat,
            "lng": lng,
            "params": ",".join(params),
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "source": "sg",
        }
        logger.info(
            "Fetching Stormglass forecast for lat={lat}, lng={lng}, start={start}, end={end}",
            lat=lat,
            lng=lng,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._session.get(
                    _STORMGLASS_ENDPOINT,
                    headers={"Authorization": self._api_key},
                    params=query_params,
                    timeout=60,
                )
                response.raise_for_status()
                break  # success
            except requests.HTTPError as exc:
                raise StormglassAPIError(
                    f"Stormglass HTTP error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF_S[attempt - 1]
                    logger.warning(
                        "Stormglass request failed (attempt {a}/{total}), retrying in {w}s: {err}",
                        a=attempt,
                        total=_MAX_RETRIES,
                        w=wait,
                        err=exc,
                    )
                    time.sleep(wait)
        else:
            raise StormglassAPIError(
                f"Stormglass request failed after {_MAX_RETRIES} attempts: {last_exc}"
            ) from last_exc

        try:
            payload: dict[str, object] = response.json()
        except ValueError as exc:
            raise StormglassAPIError(f"Invalid JSON from Stormglass: {exc}") from exc

        hours = payload.get("hours")
        if not isinstance(hours, list):
            raise StormglassAPIError(f"Unexpected Stormglass payload structure: {payload!r}")

        hours_list: list[object] = cast("list[object]", hours)
        hour_dicts: list[dict[str, object]] = []
        for item in hours_list:
            if not isinstance(item, dict):
                raise StormglassAPIError(f"Expected dict for hour entry, got {type(item)}")
            hour_dicts.append(cast("dict[str, object]", item))

        logger.info("Received {n} hourly records from Stormglass", n=len(hour_dicts))
        return [self._parse_hour(hour) for hour in hour_dicts]

    def _parse_hour(self, hour: dict[str, object]) -> WeatherDataPoint:
        """Parse a single 'hours' entry from the Stormglass response.

        Args:
            hour: Typed dictionary for one hourly record (pre-validated as dict).

        Returns:
            A WeatherDataPoint with values extracted from the 'sg' source,
            with fallback to the first available source.

        Raises:
            StormglassAPIError: If the hour entry is missing 'time'.
        """
        raw_time = hour.get("time")
        if not raw_time:
            raise StormglassAPIError(f"Missing 'time' in hour entry: {hour!r}")

        def _extract(key: str) -> float | None:
            raw_sources = hour.get(key)
            if not isinstance(raw_sources, dict):
                return None
            sources: dict[str, object] = cast("dict[str, object]", raw_sources)
            sg_val = sources.get("sg")
            if sg_val is not None and isinstance(sg_val, int | float):
                return float(sg_val)
            for val in sources.values():
                if val is not None and isinstance(val, int | float):
                    return float(val)
            return None

        return WeatherDataPoint(
            time=datetime.fromisoformat(str(raw_time).replace("Z", "+00:00")),
            wave_height=_extract("waveHeight"),
            wave_period=_extract("wavePeriod"),
            wind_speed_ms=_extract("windSpeed"),
            wind_direction=_extract("windDirection"),
            swell_height=_extract("swellHeight"),
            swell_direction=_extract("swellDirection"),
            swell_period=_extract("swellPeriod"),
        )
