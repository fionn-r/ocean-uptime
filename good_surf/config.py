"""Surf spot configuration and quality thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SurfConfig:
    """All configurable thresholds and location settings for the surf check.

    Attributes:
        lat: Latitude of the surf spot.
        lng: Longitude of the surf spot.
        location_name: Human-readable name used in notifications (e.g. "Bells Beach, VIC").
        timezone: IANA timezone string for the surf location.
        min_swell_height_m: Minimum acceptable swell height in metres.
        max_wind_speed_kts: Maximum acceptable wind speed in knots.
        offshore_wind_dir_min_deg: Minimum bearing (degrees) for offshore wind.
        offshore_wind_dir_max_deg: Maximum bearing (degrees) for offshore wind.
        daylight_start_hour: Hour (local) to start analysing (inclusive).
        daylight_end_hour: Hour (local) to stop analysing (exclusive).
        forecast_days: Number of days ahead to fetch.
        stormglass_params: Stormglass weather parameters to request.
    """

    lat: float
    lng: float
    location_name: str
    timezone: str
    min_swell_height_m: float
    max_wind_speed_kts: float
    offshore_wind_dir_min_deg: float
    offshore_wind_dir_max_deg: float
    daylight_start_hour: int
    daylight_end_hour: int
    forecast_days: int
    stormglass_params: tuple[str, ...] = field(
        default=(
            "waveHeight",
            "wavePeriod",
            "windSpeed",
            "windDirection",
            "swellHeight",
            "swellDirection",
            "swellPeriod",
        )
    )
