"""Slack message formatting and notification delivery."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from good_surf.analysis import degrees_to_cardinal
from good_surf.exceptions import SlackNotificationError
from good_surf.models import DayForecast


def format_slack_message(day_forecasts: list[DayForecast], location_name: str) -> str:
    """Build a Slack-markdown formatted notification message.

    Args:
        day_forecasts: Days that contain at least one good surf window.
        location_name: Human-readable name of the surf spot.

    Returns:
        A multi-line string using Slack mrkdwn formatting.
    """
    good_day_count = sum(1 for d in day_forecasts if d.good_windows)
    lines: list[str] = [
        f"*Good Surf Alert - {location_name}*",
        "",
        f"Conditions look good for *{good_day_count} day(s)* in the next 5-day forecast.",
        "",
    ]

    for day in day_forecasts:
        if not day.good_windows:
            continue
        lines.append(f"*{day.forecast_date.strftime('%A, %d %B %Y')}*")
        for i, window in enumerate(day.good_windows, start=1):
            swell_card = degrees_to_cardinal(window.swell_direction_deg)
            wind_card = degrees_to_cardinal(window.wind_direction_deg)
            lines += [
                f">*Window {i}: {window.start_time.strftime('%H:%M')}"
                f" - {window.end_time.strftime('%H:%M')} AWST*",
                f">  Swell: *{window.swell_height_m:.1f} m*"
                f" from {swell_card} ({window.swell_direction_deg:.0f} deg)",
                f">  Wave period: *{window.wave_period_s:.1f} s*",
                f">  Wind: *{window.wind_speed_kts:.1f} kts*"
                f" from {wind_card} ({window.wind_direction_deg:.0f} deg)",
                f">  Swell period: *{window.swell_period_s:.1f} s*",
                "",
            ]

    lines.append("_Forecast data provided by Stormglass.io_")
    return "\n".join(lines)


class SlackNotifier:
    """Sends surf alert messages and chart images to a Slack channel.

    Args:
        channel_id: Slack channel ID to post to (e.g. C01234ABCDE).
        client: Initialised slack_sdk.WebClient instance.
    """

    def __init__(self, channel_id: str, client: WebClient, location_name: str) -> None:
        self._channel_id = channel_id
        self._client = client
        self._location_name = location_name

    def send_alert(self, chart_path: Path, day_forecasts: list[DayForecast]) -> None:
        """Upload the chart and post a detailed surf conditions message to Slack.

        Args:
            chart_path: Path to the PNG chart file to upload.
            day_forecasts: Forecast days that contain good surf windows.

        Raises:
            SlackNotificationError: If the Slack API call fails.
        """
        message = format_slack_message(day_forecasts, self._location_name)
        logger.info(
            "Uploading chart {path} to Slack channel {ch}",
            path=chart_path,
            ch=self._channel_id,
        )
        try:
            self._client.conversations_join(channel=self._channel_id)  # type: ignore[reportUnknownMemberType]
            logger.debug("Bot joined/already in channel {ch}", ch=self._channel_id)
        except SlackApiError as exc:
            # Private channels can't be self-joined; log and proceed anyway.
            logger.warning(
                "Could not auto-join channel {ch}: {err} (bot may already be a member)",
                ch=self._channel_id,
                err=exc.response.get("error"),  # type: ignore[reportUnknownMemberType]
            )
        try:
            response = self._client.files_upload_v2(  # type: ignore[reportUnknownMemberType]
                channel=self._channel_id,
                file=str(chart_path),
                filename=chart_path.name,
                title=f"Surf Forecast - {chart_path.stem.replace('surf_', '')}",
                initial_comment=message,
            )
            file_meta = response.get("file", {})
            fid = file_meta.get("id") if file_meta else None
            logger.info("Slack upload complete: file_id={fid}", fid=fid)
        except SlackApiError as exc:
            raise SlackNotificationError(f"Slack API error: {exc.response['error']}") from exc
