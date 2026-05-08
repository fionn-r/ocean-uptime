"""Unit tests for the good_surf package."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

from good_surf.analysis import (
    analyze_forecasts,
    build_surf_window,
    degrees_to_cardinal,
    filter_daylight_points,
    group_into_3h_windows,
    is_window_good,
    ms_to_knots,
)
from good_surf.chart import ChartGenerator
from good_surf.config import SurfConfig
from good_surf.exceptions import ChartGenerationError
from good_surf.models import DayForecast, SurfWindow, WeatherDataPoint
from good_surf.notifier import SlackNotifier, format_slack_message
from good_surf.stormglass import StormglassClient

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

PERTH_TZ = ZoneInfo("Australia/Perth")
BASE_CONFIG = SurfConfig(
    lat=-181.9961,
    lng=318.7537,
    location_name="Shipsterns",
    timezone="Australia/Perth",
    min_swell_height_m=1.5,
    max_wind_speed_kts=15.0,
    offshore_wind_dir_min_deg=45.0,
    offshore_wind_dir_max_deg=135.0,
    daylight_start_hour=6,
    daylight_end_hour=18,
    forecast_days=5,
)

# A UTC datetime that corresponds to 07:00 AWST (Perth = UTC+8)
# 2026-01-15 23:00 UTC → 2026-01-16 07:00 AWST
_BASE_UTC = datetime(2026, 1, 15, 23, 0, 0, tzinfo=UTC)


def _make_point(
    hour_utc: datetime,
    swell_height: float | None = 2.0,
    wave_period: float | None = 12.0,
    wind_speed_ms: float | None = 5.0,  # ~9.7 kts — within 15 kt limit
    wind_direction: float | None = 90.0,  # E — within offshore 45-135
    swell_direction: float | None = 270.0,
    swell_period: float | None = 10.0,
) -> WeatherDataPoint:
    """Create a WeatherDataPoint at the given UTC time with sensible defaults."""
    return WeatherDataPoint(
        time=hour_utc,
        swell_height=swell_height,
        wave_period=wave_period,
        wind_speed_ms=wind_speed_ms,
        wind_direction=wind_direction,
        swell_direction=swell_direction,
        swell_period=swell_period,
    )


def _daylight_window_points(base: datetime = _BASE_UTC) -> list[WeatherDataPoint]:
    """Return 3 data points at 07:00, 08:00, 09:00 AWST (23:00, 00:00, 01:00 UTC)."""
    return [
        _make_point(base),
        _make_point(base + timedelta(hours=1)),
        _make_point(base + timedelta(hours=2)),
    ]


# ---------------------------------------------------------------------------
# ms_to_knots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ms", "expected_kts"),
    [
        (0.0, 0.0),
        (1.0, 1.94384),
        (5.144, None),  # approx 10 kts; tested separately below
        (10.0, None),  # approx 19.44 kts; tested separately below
    ],
)
def test_ms_to_knots(ms: float, expected_kts: float | None) -> None:
    """ms_to_knots should convert metres per second to knots correctly."""
    if expected_kts is not None:
        assert ms_to_knots(ms) == expected_kts


def test_ms_to_knots_approximate() -> None:
    """ms_to_knots approximate cases verified with math.isclose."""
    assert math.isclose(ms_to_knots(5.144), 10.0, abs_tol=0.01)
    assert math.isclose(ms_to_knots(10.0), 19.44, abs_tol=0.01)


# ---------------------------------------------------------------------------
# degrees_to_cardinal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deg", "expected"),
    [
        (0.0, "N"),
        (360.0, "N"),
        (45.0, "NE"),
        (90.0, "E"),
        (135.0, "SE"),
        (180.0, "S"),
        (225.0, "SW"),
        (270.0, "W"),
        (315.0, "NW"),
        (22.5, "NNE"),
        (67.5, "ENE"),
        (112.5, "ESE"),
        (157.5, "SSE"),
        (202.5, "SSW"),
        (247.5, "WSW"),
        (292.5, "WNW"),
        (337.5, "NNW"),
        (350.0, "N"),  # rounds to N
        (361.0, "N"),  # wraps modulo 360
    ],
)
def test_degrees_to_cardinal(deg: float, expected: str) -> None:
    """degrees_to_cardinal should return the nearest 16-point direction."""
    assert degrees_to_cardinal(deg) == expected


# ---------------------------------------------------------------------------
# filter_daylight_points
# ---------------------------------------------------------------------------


def test_filter_daylight_points_keeps_daylight() -> None:
    """Points between 06:00 and 17:59 AWST should be kept."""
    # 22:00 UTC = 06:00 AWST  (should be included, start_hour=6)
    # 23:00 UTC = 07:00 AWST  (included)
    # 10:00 UTC = 18:00 AWST  (excluded, end_hour=18 is exclusive)
    pts = [
        _make_point(datetime(2026, 1, 15, 22, 0, tzinfo=UTC)),  # 06:00 AWST ✓
        _make_point(datetime(2026, 1, 15, 23, 0, tzinfo=UTC)),  # 07:00 AWST ✓
        _make_point(datetime(2026, 1, 16, 9, 0, tzinfo=UTC)),  # 17:00 AWST ✓
        _make_point(datetime(2026, 1, 16, 10, 0, tzinfo=UTC)),  # 18:00 AWST ✗
        _make_point(datetime(2026, 1, 15, 21, 0, tzinfo=UTC)),  # 05:00 AWST ✗
    ]
    result = filter_daylight_points(pts, PERTH_TZ, 6, 18)
    assert len(result) == 3
    result_hours_awst = [pt.time.astimezone(PERTH_TZ).hour for pt in result]
    assert result_hours_awst == [6, 7, 17]


def test_filter_daylight_points_empty_input() -> None:
    """Empty input should return empty list."""
    assert filter_daylight_points([], PERTH_TZ, 6, 18) == []


# ---------------------------------------------------------------------------
# group_into_3h_windows
# ---------------------------------------------------------------------------


def test_group_into_3h_windows_basic() -> None:
    """Points in the 06:00-08:59 AWST bucket should be grouped together."""
    # 22:00 UTC = 06:00 AWST, 23:00 UTC = 07:00 AWST, 00:00 UTC = 08:00 AWST
    pts = [
        _make_point(datetime(2026, 1, 15, 22, 0, tzinfo=UTC)),  # 06:00 AWST → bucket 6
        _make_point(datetime(2026, 1, 15, 23, 0, tzinfo=UTC)),  # 07:00 AWST → bucket 6
        _make_point(datetime(2026, 1, 16, 0, 0, tzinfo=UTC)),  # 08:00 AWST → bucket 6
        _make_point(datetime(2026, 1, 16, 1, 0, tzinfo=UTC)),  # 09:00 AWST → bucket 9
    ]
    windows = group_into_3h_windows(pts, PERTH_TZ)
    assert len(windows) == 2
    assert len(windows[0]) == 3  # 06, 07, 08
    assert len(windows[1]) == 1  # 09


def test_group_into_3h_windows_empty() -> None:
    """Empty input should return empty list."""
    assert group_into_3h_windows([], PERTH_TZ) == []


# ---------------------------------------------------------------------------
# is_window_good
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("swell_height", "wind_speed_ms", "wind_direction", "expected"),
    [
        # All conditions met
        (2.0, 5.0, 90.0, True),
        # Swell too low
        (1.0, 5.0, 90.0, False),
        # Wind too high (10 m/s = ~19.4 kts > 15 kts)
        (2.0, 10.0, 90.0, False),
        # Wind direction out of offshore range (S = 180° > 135°)
        (2.0, 5.0, 180.0, False),
        # Wind direction at lower bound (exactly 45°)
        (2.0, 5.0, 45.0, True),
        # Wind direction at upper bound (exactly 135°)
        (2.0, 5.0, 135.0, True),
    ],
)
def test_is_window_good_single_point(
    swell_height: float,
    wind_speed_ms: float,
    wind_direction: float,
    expected: bool,
) -> None:
    """is_window_good should correctly evaluate single-point windows."""
    window = [
        _make_point(
            _BASE_UTC,
            swell_height=swell_height,
            wind_speed_ms=wind_speed_ms,
            wind_direction=wind_direction,
        )
    ]
    assert is_window_good(window, BASE_CONFIG) == expected


def test_is_window_good_empty() -> None:
    """An empty window should return False."""
    assert is_window_good([], BASE_CONFIG) is False


def test_is_window_good_none_field() -> None:
    """A point with a None critical field should fail."""
    window = [_make_point(_BASE_UTC, swell_height=None)]
    assert is_window_good(window, BASE_CONFIG) is False


def test_is_window_good_all_points_must_pass() -> None:
    """All points in a window must meet thresholds — one failure fails the window."""
    good_pt = _make_point(_BASE_UTC)
    bad_pt = _make_point(_BASE_UTC + timedelta(hours=1), swell_height=0.5)
    assert is_window_good([good_pt, bad_pt], BASE_CONFIG) is False


# ---------------------------------------------------------------------------
# build_surf_window
# ---------------------------------------------------------------------------


def test_build_surf_window_averages() -> None:
    """build_surf_window should compute averages and set correct local times."""
    # 23:00 UTC = 07:00 AWST, 00:00 UTC = 08:00 AWST
    pts = [
        _make_point(
            datetime(2026, 1, 15, 23, 0, tzinfo=UTC),
            swell_height=2.0,
            wave_period=10.0,
            wind_speed_ms=4.0,
        ),
        _make_point(
            datetime(2026, 1, 16, 0, 0, tzinfo=UTC),
            swell_height=3.0,
            wave_period=14.0,
            wind_speed_ms=6.0,
        ),
    ]
    window = build_surf_window(pts, PERTH_TZ)

    assert math.isclose(window.swell_height_m, 2.5)
    assert math.isclose(window.wave_period_s, 12.0)
    assert math.isclose(window.wind_speed_kts, ms_to_knots(5.0))
    # start = 07:00 AWST
    assert window.start_time.astimezone(PERTH_TZ).hour == 7
    # end = last point + 1h = 09:00 AWST
    assert window.end_time.astimezone(PERTH_TZ).hour == 9


def test_build_surf_window_empty_raises() -> None:
    """build_surf_window with empty list should raise ValueError."""
    with pytest.raises(ValueError, match="empty"):
        build_surf_window([], PERTH_TZ)


# ---------------------------------------------------------------------------
# Fake HTTP Session for StormglassClient
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def json(self) -> dict[str, object]:
        """Return the fixture payload."""
        return self._data

    def raise_for_status(self) -> None:
        """No-op: fake responses always succeed."""


class FakeSession:
    """Minimal stand-in for requests.Session."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.last_params: dict[str, object] = {}

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        """Capture the request and return the fixture payload."""
        self.last_params = params
        return FakeResponse(self._payload)


_FIXTURE_PAYLOAD: dict[str, object] = {
    "hours": [
        {
            "time": "2026-01-15T23:00:00+00:00",
            "waveHeight": {"sg": 1.8},
            "wavePeriod": {"sg": 11.0},
            "windSpeed": {"sg": 4.5},
            "windDirection": {"sg": 90.0},
            "swellHeight": {"sg": 1.8},
            "swellDirection": {"sg": 270.0},
            "swellPeriod": {"sg": 9.5},
        },
        {
            "time": "2026-01-16T00:00:00+00:00",
            "waveHeight": {"sg": 2.0},
            "wavePeriod": {"sg": 12.0},
            "windSpeed": {"sg": 5.0},
            "windDirection": {"sg": 100.0},
            "swellHeight": {"sg": 2.0},
            "swellDirection": {"sg": 265.0},
            "swellPeriod": {"sg": 10.0},
        },
    ],
    "meta": {"dailyQuota": 10, "requestCount": 1},
}


# ---------------------------------------------------------------------------
# StormglassClient
# ---------------------------------------------------------------------------


def test_stormglass_client_fetch_parses_points() -> None:
    """fetch_forecast should return correct WeatherDataPoint objects from fixture."""
    session = FakeSession(_FIXTURE_PAYLOAD)
    client = StormglassClient(api_key="test-key", session=session)  # type: ignore[arg-type]

    start = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 20, 0, 0, tzinfo=UTC)

    points = client.fetch_forecast(
        start=start, end=end, lat=-31.9961, lng=115.7537, params=("swellHeight",)
    )

    assert len(points) == 2
    assert points[0].swell_height == 1.8
    assert points[1].swell_height == 2.0
    assert points[0].wind_direction == 90.0


def test_stormglass_client_source_fallback() -> None:
    """_parse_hour should fall back to the first non-sg source if sg is missing."""
    payload_no_sg: dict[str, object] = {
        "hours": [
            {
                "time": "2026-01-15T23:00:00+00:00",
                "swellHeight": {"noaa": 1.5},  # no 'sg' key
                "wavePeriod": {},  # empty - should yield None
                "windSpeed": None,  # wrong type - should yield None
                "windDirection": {"sg": None, "noaa": 120.0},  # sg is None, fallback to noaa
                "waveHeight": {"sg": 1.2},
                "swellDirection": {"sg": 270.0},
                "swellPeriod": {"sg": 8.0},
            }
        ],
        "meta": {},
    }
    session = FakeSession(payload_no_sg)
    client = StormglassClient(api_key="key", session=session)  # type: ignore[arg-type]
    points = client.fetch_forecast(
        start=datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 16, 0, 0, tzinfo=UTC),
        lat=-31.9961,
        lng=115.7537,
        params=("swellHeight",),
    )
    assert points[0].swell_height == 1.5  # fallback to noaa
    assert points[0].wave_period is None  # empty dict -> None
    assert points[0].wind_speed_ms is None  # wrong type -> None
    assert points[0].wind_direction == 120.0  # sg=None, noaa fallback


def test_stormglass_client_retries_on_timeout() -> None:
    """fetch_forecast should retry on RequestException and succeed if a later attempt works."""

    class FlakySession:
        """Fails on the first call, succeeds on the second."""

        def __init__(self) -> None:
            self.call_count = 0

        def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, object],
            timeout: int,
        ) -> FakeResponse:
            self.call_count += 1
            if self.call_count == 1:
                raise requests.exceptions.Timeout("timed out")
            return FakeResponse(_FIXTURE_PAYLOAD)

    session = FlakySession()
    # Patch sleep so the test doesn't actually wait
    import unittest.mock as mock

    with mock.patch("good_surf.stormglass.time.sleep"):
        client = StormglassClient(api_key="key", session=session)  # type: ignore[arg-type]
        points = client.fetch_forecast(
            start=datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
            end=datetime(2026, 1, 16, 0, 0, tzinfo=UTC),
            lat=-31.9961,
            lng=115.7537,
            params=("swellHeight",),
        )

    assert session.call_count == 2
    assert len(points) == 2


# ---------------------------------------------------------------------------
# analyze_forecasts integration
# ---------------------------------------------------------------------------


def test_analyze_forecasts_detects_good_day() -> None:
    """analyze_forecasts should find a qualifying window when all thresholds are met."""
    # Build a set of 3 consecutive good-condition points in AWST daylight
    # 22:00, 23:00 UTC = 06:00, 07:00 AWST (bucket 6 = 06-08)
    # 00:00, 01:00 UTC = 08:00, 09:00 AWST (bucket 6 for 08, bucket 9 for 09)
    base = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
    pts = [_make_point(base + timedelta(hours=i)) for i in range(4)]
    forecasts = analyze_forecasts(pts, BASE_CONFIG, PERTH_TZ)

    # 22-00 UTC are on date 2026-01-16 in AWST (06-08 AWST); 01 UTC is also 2026-01-16
    good_days = [f for f in forecasts if f.good_windows]
    assert len(good_days) >= 1


def test_analyze_forecasts_no_good_day() -> None:
    """analyze_forecasts should return no good_windows when swell is too low."""
    base = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
    pts = [_make_point(base + timedelta(hours=i), swell_height=0.5) for i in range(4)]
    forecasts = analyze_forecasts(pts, BASE_CONFIG, PERTH_TZ)
    assert all(not f.good_windows for f in forecasts)


def test_analyze_forecasts_outside_daylight_ignored() -> None:
    """Points outside daylight hours should be excluded from analysis."""
    # 12:00 UTC = 20:00 AWST — outside daylight window
    bad_time = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
    pts = [_make_point(bad_time)]
    forecasts = analyze_forecasts(pts, BASE_CONFIG, PERTH_TZ)
    # Data point should be filtered out → no good windows
    assert all(not f.good_windows for f in forecasts)
    assert all(len(f.data_points) == 0 for f in forecasts)


# ---------------------------------------------------------------------------
# ChartGenerator
# ---------------------------------------------------------------------------


def test_chart_generator_creates_file(tmp_path: Path) -> None:
    """ChartGenerator.generate should save a PNG at the expected path."""
    pts = _daylight_window_points()
    windows: list[SurfWindow] = []
    day = DayForecast(
        forecast_date=date(2026, 1, 16),
        data_points=pts,
        good_windows=windows,
    )
    gen = ChartGenerator(output_dir=tmp_path)
    output = gen.generate(day, BASE_CONFIG)

    assert output.exists()
    assert output.suffix == ".png"
    assert "2026-01-16" in output.name


def test_chart_generator_with_good_windows(tmp_path: Path) -> None:
    """ChartGenerator should succeed even when good_windows are present (shading)."""
    pts = _daylight_window_points()
    window = build_surf_window(pts, PERTH_TZ)
    day = DayForecast(
        forecast_date=date(2026, 1, 16),
        data_points=pts,
        good_windows=[window],
    )
    gen = ChartGenerator(output_dir=tmp_path)
    output = gen.generate(day, BASE_CONFIG)
    assert output.exists()


def test_chart_generator_empty_data_raises(tmp_path: Path) -> None:
    """ChartGenerator.generate should raise ChartGenerationError for empty data."""
    day = DayForecast(forecast_date=date(2026, 1, 16), data_points=[], good_windows=[])
    gen = ChartGenerator(output_dir=tmp_path)
    with pytest.raises(ChartGenerationError):
        gen.generate(day, BASE_CONFIG)


# ---------------------------------------------------------------------------
# _format_slack_message
# ---------------------------------------------------------------------------


def _make_surf_window(start_hour_awst: int = 7) -> SurfWindow:
    """Build a SurfWindow at the given hour in AWST on 2026-01-16."""
    start_local = datetime(2026, 1, 16, start_hour_awst, 0, tzinfo=PERTH_TZ)
    end_local = start_local + timedelta(hours=3)
    return SurfWindow(
        start_time=start_local,
        end_time=end_local,
        swell_height_m=2.1,
        wave_period_s=11.5,
        wind_speed_kts=8.3,
        wind_direction_deg=90.0,
        swell_direction_deg=270.0,
        swell_period_s=9.8,
    )


def test_format_slack_message_contains_key_data() -> None:
    """_format_slack_message should include swell, wind, and cardinal direction info."""
    window = _make_surf_window(7)
    day = DayForecast(
        forecast_date=date(2026, 1, 16),
        data_points=[],
        good_windows=[window],
    )
    msg = format_slack_message([day], "The beach")

    assert "Cottesloe Beach" in msg
    assert "2.1 m" in msg
    assert "8.3 kts" in msg
    assert "11.5 s" in msg
    assert "90" in msg  # wind direction degrees
    assert "E" in msg  # cardinal for 90°
    assert "W" in msg  # cardinal for 270° swell
    assert "16 January" in msg or "Jan" in msg or "2026" in msg


def test_format_slack_message_no_good_days() -> None:
    """_format_slack_message with no good windows should still return a string."""
    day = DayForecast(
        forecast_date=date(2026, 1, 16),
        data_points=[],
        good_windows=[],
    )
    msg = format_slack_message([day], "Test Beach")
    assert isinstance(msg, str)


# ---------------------------------------------------------------------------
# SlackNotifier
# ---------------------------------------------------------------------------


class FakeSlackFile:
    """Fake return value for files_upload_v2."""

    def get(self, key: str, default: object = None) -> object:
        """Return a fake file metadata dict."""
        if key == "file":
            return {"id": "F123"}
        return default


class FakeWebClient:
    """Minimal fake for slack_sdk.WebClient."""

    def __init__(self) -> None:
        self.upload_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def conversations_join(self, **kwargs: object) -> None:
        """No-op fake for conversations_join."""

    def files_upload_v2(self, **kwargs: object) -> FakeSlackFile:
        """Record the call and return a fake response."""
        self.upload_calls.append(kwargs)
        return FakeSlackFile()

    def chat_postMessage(self, **kwargs: object) -> None:
        """Record the call."""
        self.post_calls.append(kwargs)


def test_slack_notifier_calls_upload(tmp_path: Path) -> None:
    """SlackNotifier.send_alert should call files_upload_v2 with correct args."""
    chart = tmp_path / "surf_2026-01-16.png"
    chart.write_bytes(b"PNG_DATA")

    fake_client = FakeWebClient()
    notifier = SlackNotifier(channel_id="C123", client=fake_client, location_name="Test Beach")  # type: ignore[arg-type]

    window = _make_surf_window(9)
    day = DayForecast(
        forecast_date=date(2026, 1, 16),
        data_points=[],
        good_windows=[window],
    )
    notifier.send_alert(chart_path=chart, day_forecasts=[day])

    assert len(fake_client.upload_calls) == 1
    call = fake_client.upload_calls[0]
    assert call["channel"] == "C123"
    assert call["file"] == str(chart)
    assert "initial_comment" in call
    assert isinstance(call["initial_comment"], str)


def test_slack_notifier_send_error_posts_message() -> None:
    """SlackNotifier.send_error should call chat_postMessage with the error details."""
    fake_client = FakeWebClient()
    notifier = SlackNotifier(channel_id="C123", client=fake_client, location_name="Test Beach")  # type: ignore[arg-type]

    notifier.send_error(ValueError("something broke"))

    assert len(fake_client.post_calls) == 1
    call = fake_client.post_calls[0]
    assert call["channel"] == "C123"
    assert "ValueError" in str(call["text"])
    assert "something broke" in str(call["text"])
