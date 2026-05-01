"""Pure analysis functions: unit conversions, filtering, windowing, and scoring."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from good_surf.config import SurfConfig
from good_surf.models import DayForecast, SurfWindow, WeatherDataPoint

_CARDINAL_DIRECTIONS: tuple[str, ...] = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)
_CARDINAL_SECTOR_SIZE: float = 360.0 / len(_CARDINAL_DIRECTIONS)


def ms_to_knots(ms: float) -> float:
    """Convert metres per second to knots.

    Args:
        ms: Speed in metres per second.

    Returns:
        Speed in knots.
    """
    return ms * 1.94384


def degrees_to_cardinal(deg: float) -> str:
    """Convert a bearing in degrees to the nearest 16-point cardinal direction.

    Args:
        deg: Bearing in degrees (0-360).

    Returns:
        A cardinal direction string such as 'N', 'NNE', 'SW', etc.
    """
    normalised = deg % 360.0
    index = round(normalised / _CARDINAL_SECTOR_SIZE) % len(_CARDINAL_DIRECTIONS)
    return _CARDINAL_DIRECTIONS[index]


def filter_daylight_points(
    points: list[WeatherDataPoint],
    tz: ZoneInfo,
    start_hour: int,
    end_hour: int,
) -> list[WeatherDataPoint]:
    """Return only data points whose local time falls within the daylight window.

    Args:
        points: All hourly data points (UTC-aware).
        tz: The local timezone (e.g. ZoneInfo("Australia/Perth")).
        start_hour: Inclusive start hour in local time (e.g. 6 for 6 AM).
        end_hour: Exclusive end hour in local time (e.g. 18 for 6 PM).

    Returns:
        Filtered list containing only daylight-window points.
    """
    result: list[WeatherDataPoint] = []
    for pt in points:
        local_dt = pt.time.astimezone(tz)
        if start_hour <= local_dt.hour < end_hour:
            result.append(pt)
    logger.debug(
        "Daylight filter: {kept}/{total} points kept (local {start}:00-{end}:00)",
        kept=len(result),
        total=len(points),
        start=start_hour,
        end=end_hour,
    )
    return result


def group_into_3h_windows(
    points: list[WeatherDataPoint],
    tz: ZoneInfo,
) -> list[list[WeatherDataPoint]]:
    """Group data points into consecutive 3-hour buckets based on local time.

    Buckets are aligned to the nearest 3-hour boundary (0, 3, 6, …), so
    daylight buckets for a 6-18 window are: 6-9, 9-12, 12-15, 15-18 AWST.

    Args:
        points: Hourly data points (UTC-aware), already filtered to daylight window.
        tz: Local timezone used for bucket assignment.

    Returns:
        A list of groups; each group is a list of WeatherDataPoint.
    """
    buckets: dict[int, list[WeatherDataPoint]] = {}
    for pt in points:
        local_hour = pt.time.astimezone(tz).hour
        bucket_key = (local_hour // 3) * 3
        buckets.setdefault(bucket_key, []).append(pt)

    windows = [pts for _, pts in sorted(buckets.items())]
    logger.debug("Grouped {n} data points into {w} 3-hour windows", n=len(points), w=len(windows))
    return windows


def is_window_good(window: list[WeatherDataPoint], config: SurfConfig) -> bool:
    """Return True if every point in the window meets all surf quality thresholds.

    A window qualifies when for each data point:
      - swell_height >= min_swell_height_m
      - wind_speed (converted to knots) <= max_wind_speed_kts
      - wind_direction is within [offshore_wind_dir_min_deg, offshore_wind_dir_max_deg]

    If any required field is None for a point, that point fails the check.

    Args:
        window: A list of WeatherDataPoint covering a 3-hour period.
        config: Surf quality thresholds.

    Returns:
        True if the window is surf-worthy, False otherwise.
    """
    if not window:
        return False
    for pt in window:
        if pt.swell_height is None or pt.wind_speed_ms is None or pt.wind_direction is None:
            logger.debug("Window point at {t} has None value, window fails", t=pt.time)
            return False
        swell_ok = pt.swell_height >= config.min_swell_height_m
        wind_kts = ms_to_knots(pt.wind_speed_ms)
        wind_speed_ok = wind_kts <= config.max_wind_speed_kts
        wind_dir_ok = (
            config.offshore_wind_dir_min_deg
            <= pt.wind_direction
            <= config.offshore_wind_dir_max_deg
        )
        if not (swell_ok and wind_speed_ok and wind_dir_ok):
            logger.debug(
                "Point at {t} fails: swell={s:.2f}m (ok={sok}), wind={w:.1f}kts (ok={wok}),"
                " dir={d:.0f}deg (ok={dok})",
                t=pt.time,
                s=pt.swell_height,
                sok=swell_ok,
                w=wind_kts,
                wok=wind_speed_ok,
                d=pt.wind_direction,
                dok=wind_dir_ok,
            )
            return False
    return True


def build_surf_window(window_points: list[WeatherDataPoint], tz: ZoneInfo) -> SurfWindow:
    """Compute a SurfWindow from a list of qualifying data points (averaged values).

    Args:
        window_points: Non-empty list of WeatherDataPoint that all passed threshold checks.
        tz: Local timezone for start/end time representation.

    Returns:
        A SurfWindow with averaged metric values and local start/end times.

    Raises:
        ValueError: If window_points is empty or required fields are None.
    """
    if not window_points:
        raise ValueError("window_points must not be empty")

    def _avg(values: list[float | None]) -> float:
        valid = [v for v in values if v is not None]
        if not valid:
            raise ValueError("No valid (non-None) values to average")
        return sum(valid) / len(valid)

    start_local = window_points[0].time.astimezone(tz)
    end_local = window_points[-1].time.astimezone(tz) + timedelta(hours=1)

    return SurfWindow(
        start_time=start_local,
        end_time=end_local,
        swell_height_m=_avg([pt.swell_height for pt in window_points]),
        wave_period_s=_avg([pt.wave_period for pt in window_points]),
        wind_speed_kts=ms_to_knots(_avg([pt.wind_speed_ms for pt in window_points])),
        wind_direction_deg=_avg([pt.wind_direction for pt in window_points]),
        swell_direction_deg=_avg([pt.swell_direction for pt in window_points]),
        swell_period_s=_avg([pt.swell_period for pt in window_points]),
    )


def analyze_forecasts(
    points: list[WeatherDataPoint],
    config: SurfConfig,
    tz: ZoneInfo,
) -> list[DayForecast]:
    """Group forecast points by local date, find qualifying surf windows per day.

    Args:
        points: All hourly forecast points across the full 5-day window.
        config: Surf quality thresholds and location settings.
        tz: Local timezone for date grouping and window analysis.

    Returns:
        A list of DayForecast objects (one per day), each containing only daylight
        data points and any qualifying surf windows detected.
    """
    by_date: dict[date, list[WeatherDataPoint]] = {}
    for pt in points:
        local_date = pt.time.astimezone(tz).date()
        by_date.setdefault(local_date, []).append(pt)

    forecasts: list[DayForecast] = []
    for forecast_date in sorted(by_date):
        day_points = by_date[forecast_date]
        daylight = filter_daylight_points(
            day_points, tz, config.daylight_start_hour, config.daylight_end_hour
        )
        good_windows: list[SurfWindow] = []
        for window in group_into_3h_windows(daylight, tz):
            if is_window_good(window, config):
                good_windows.append(build_surf_window(window, tz))
                logger.info(
                    "Good surf window found on {d}: {s} - {e}",
                    d=forecast_date,
                    s=good_windows[-1].start_time.strftime("%H:%M"),
                    e=good_windows[-1].end_time.strftime("%H:%M"),
                )
        forecasts.append(
            DayForecast(
                forecast_date=forecast_date,
                data_points=daylight,
                good_windows=good_windows,
            )
        )
    logger.info(
        "Analysis complete: {days} days, {good} with good surf",
        days=len(forecasts),
        good=sum(1 for f in forecasts if f.good_windows),
    )
    return forecasts
