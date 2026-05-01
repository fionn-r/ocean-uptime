"""Custom exception types for the good-surf-notification application."""

from __future__ import annotations


class StormglassAPIError(Exception):
    """Raised when the Stormglass API returns an error or unexpected response."""


class SlackNotificationError(Exception):
    """Raised when the Slack API call fails."""


class ChartGenerationError(Exception):
    """Raised when matplotlib chart generation fails."""


class MissingConfigError(Exception):
    """Raised when a required environment variable is missing."""
