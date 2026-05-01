"""CLI entry point and application orchestration."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from loguru import logger
from slack_sdk import WebClient

from good_surf.analysis import analyze_forecasts
from good_surf.chart import ChartGenerator
from good_surf.config import SurfConfig
from good_surf.exceptions import MissingConfigError
from good_surf.notifier import SlackNotifier
from good_surf.stormglass import StormglassClient


def _require_env(name: str) -> str:
    """Read a required environment variable or raise MissingConfigError.

    Args:
        name: Name of the environment variable.

    Returns:
        The value of the environment variable.

    Raises:
        MissingConfigError: If the variable is not set or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingConfigError(f"Required environment variable '{name}' is not set")
    return value


def main() -> None:
    """Entry point: fetch forecast, detect good surf days, notify via Slack.

    Loads secrets from a .env file (if present), builds all service objects,
    fetches the 5-day marine forecast, analyses conditions, and - if any
    qualifying day is found - generates a chart and sends a Slack alert.

    With ``--force``, skips condition checks and sends the alert for the first
    available forecast day. Intended for local testing.
    """
    parser = argparse.ArgumentParser(
        description="Check the surf forecast and send a Slack notification on good days.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Location (required)
    parser.add_argument("--lat", type=float, required=True, help="Latitude of the surf spot.")
    parser.add_argument("--lng", type=float, required=True, help="Longitude of the surf spot.")
    parser.add_argument(
        "--location-name",
        required=True,
        help="Human-readable location name used in Slack notifications (e.g. 'Bells Beach, VIC').",
    )
    parser.add_argument(
        "--timezone",
        required=True,
        help="IANA timezone for the surf spot (e.g. 'Australia/Eucla', 'Australia/Sydney').",
    )

    # Thresholds (optional with sensible defaults)
    parser.add_argument(
        "--min-swell-height",
        type=float,
        default=1.5,
        dest="min_swell_height_m",
        help="Minimum swell height in metres.",
    )
    parser.add_argument(
        "--max-wind-speed",
        type=float,
        default=15.0,
        dest="max_wind_speed_kts",
        help="Maximum acceptable wind speed in knots.",
    )
    parser.add_argument(
        "--offshore-wind-min",
        type=float,
        default=45.0,
        dest="offshore_wind_dir_min_deg",
        help="Minimum bearing (degrees) for offshore wind.",
    )
    parser.add_argument(
        "--offshore-wind-max",
        type=float,
        default=135.0,
        dest="offshore_wind_dir_max_deg",
        help="Maximum bearing (degrees) for offshore wind.",
    )
    parser.add_argument(
        "--daylight-start",
        type=int,
        default=6,
        dest="daylight_start_hour",
        help="Local hour to start analysing (inclusive, 0-23).",
    )
    parser.add_argument(
        "--daylight-end",
        type=int,
        default=18,
        dest="daylight_end_hour",
        help="Local hour to stop analysing (exclusive, 0-23).",
    )
    parser.add_argument(
        "--forecast-days",
        type=int,
        default=5,
        dest="forecast_days",
        help="Number of days ahead to fetch.",
    )

    # Behaviour flags
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send alert for the first forecast day regardless of surf conditions.",
    )
    args = parser.parse_args()

    load_dotenv()
    logger.info("Starting good-surf-notification (force={force})", force=args.force)

    api_key = _require_env("STORMGLASS_API_KEY")
    slack_token = _require_env("SLACK_BOT_TOKEN")
    slack_channel = _require_env("SLACK_CHANNEL_ID")

    config = SurfConfig(
        lat=args.lat,
        lng=args.lng,
        location_name=args.location_name,
        timezone=args.timezone,
        min_swell_height_m=args.min_swell_height_m,
        max_wind_speed_kts=args.max_wind_speed_kts,
        offshore_wind_dir_min_deg=args.offshore_wind_dir_min_deg,
        offshore_wind_dir_max_deg=args.offshore_wind_dir_max_deg,
        daylight_start_hour=args.daylight_start_hour,
        daylight_end_hour=args.daylight_end_hour,
        forecast_days=args.forecast_days,
    )
    tz = ZoneInfo(config.timezone)

    now_utc = datetime.now(UTC)
    forecast_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    forecast_end = forecast_start + timedelta(days=config.forecast_days)

    with requests.Session() as session:
        stormglass = StormglassClient(api_key=api_key, session=session)
        points = stormglass.fetch_forecast(
            start=forecast_start,
            end=forecast_end,
            lat=config.lat,
            lng=config.lng,
            params=config.stormglass_params,
        )

    day_forecasts = analyze_forecasts(points, config, tz)
    good_days = [d for d in day_forecasts if d.good_windows]

    if args.force:
        if not day_forecasts:
            logger.warning("--force set but no forecast data was returned. Exiting.")
            return
        force_day = day_forecasts[0]
        logger.warning(
            "--force flag set: sending alert for {d} (ignoring thresholds)",
            d=force_day.forecast_date,
        )
        good_days = [force_day]

    if not good_days:
        logger.info("No good surf days in the 5-day forecast. No Slack message sent.")
        return

    logger.info(
        "Found {n} good surf day(s): {dates}",
        n=len(good_days),
        dates=[str(d.forecast_date) for d in good_days],
    )

    chart_gen = ChartGenerator()
    chart_path = chart_gen.generate(good_days[0], config)

    notifier = SlackNotifier(
        channel_id=slack_channel,
        client=WebClient(token=slack_token),
        location_name=config.location_name,
    )
    notifier.send_alert(chart_path=chart_path, day_forecasts=good_days)
    logger.info("Surf alert sent successfully.")
