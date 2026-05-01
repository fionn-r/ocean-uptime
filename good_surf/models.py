"""Pydantic data models for weather data, surf windows, and daily forecasts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, field_validator


class WeatherDataPoint(BaseModel):
    """A single hourly weather data point from Stormglass.

    Attributes:
        time: UTC-aware datetime of the observation/forecast.
        wave_height: Significant wave height in metres.
        wave_period: Wave period in seconds.
        wind_speed_ms: Wind speed in metres per second.
        wind_direction: Wind direction in degrees (meteorological convention).
        swell_height: Swell height in metres.
        swell_direction: Swell direction in degrees.
        swell_period: Swell period in seconds.
    """

    time: datetime
    wave_height: float | None = None
    wave_period: float | None = None
    wind_speed_ms: float | None = None
    wind_direction: float | None = None
    swell_height: float | None = None
    swell_direction: float | None = None
    swell_period: float | None = None

    @field_validator("time", mode="before")
    @classmethod
    def parse_utc_time(cls, v: object) -> datetime:
        """Parse ISO 8601 string or pass through a datetime, ensuring UTC awareness.

        Args:
            v: The raw value from Stormglass (typically an ISO 8601 string).

        Returns:
            A timezone-aware datetime in UTC.

        Raises:
            ValueError: If the value cannot be parsed as a datetime.
        """
        if isinstance(v, datetime):
            return v.replace(tzinfo=UTC) if v.tzinfo is None else v
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        raise ValueError(f"Cannot parse time value: {v!r}")


class SurfWindow(BaseModel):
    """Averaged conditions over a 3-hour analysis window that met all thresholds.

    Attributes:
        start_time: Start of the window (local timezone).
        end_time: End of the window (local timezone).
        swell_height_m: Average swell height in metres.
        wave_period_s: Average wave period in seconds.
        wind_speed_kts: Average wind speed in knots.
        wind_direction_deg: Average wind direction in degrees.
        swell_direction_deg: Average swell direction in degrees.
        swell_period_s: Average swell period in seconds.
    """

    start_time: datetime
    end_time: datetime
    swell_height_m: float
    wave_period_s: float
    wind_speed_kts: float
    wind_direction_deg: float
    swell_direction_deg: float
    swell_period_s: float


class DayForecast(BaseModel):
    """All weather data and qualifying surf windows for a single forecast day.

    Attributes:
        forecast_date: The local date being analysed.
        data_points: All hourly data points for the day in the daylight window.
        good_windows: Windows that satisfied all surf quality thresholds.
    """

    forecast_date: date
    data_points: list[WeatherDataPoint]
    good_windows: list[SurfWindow]
