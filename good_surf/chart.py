"""Matplotlib chart generation for daily surf forecasts."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from loguru import logger

from good_surf.analysis import ms_to_knots
from good_surf.config import SurfConfig
from good_surf.exceptions import ChartGenerationError
from good_surf.models import DayForecast


class ChartGenerator:
    """Generates a multi-panel matplotlib chart for a single surf forecast day.

    Args:
        output_dir: Directory where chart PNGs will be saved (default: /tmp).
    """

    def __init__(self, output_dir: Path = Path("/tmp")) -> None:
        self._output_dir = output_dir

    def generate(self, day_forecast: DayForecast, config: SurfConfig) -> Path:
        """Render a 4-panel chart for the given day and save it as a PNG.

        Panels:
          1. Swell Height (m) - with minimum threshold line
          2. Wave Period (s)
          3. Wind Speed (kts) - with maximum threshold line
          4. Wind Direction (degrees)

        Good surf windows are shaded green across all panels.

        Args:
            day_forecast: The day's filtered data and qualifying windows.
            config: Config thresholds used for reference lines.

        Returns:
            Path to the saved PNG file.

        Raises:
            ChartGenerationError: If chart generation or saving fails.
        """
        points = day_forecast.data_points
        if not points:
            raise ChartGenerationError(f"No data points for {day_forecast.forecast_date}")

        tz = ZoneInfo(config.timezone)
        times = [pt.time.astimezone(tz) for pt in points]

        panel_data: list[tuple[str, list[float | None], str, float | None, str]] = [
            (
                "Swell Height",
                [pt.swell_height for pt in points],
                "m",
                config.min_swell_height_m,
                "min",
            ),
            ("Wave Period", [pt.wave_period for pt in points], "s", None, ""),
            (
                "Wind Speed",
                [
                    ms_to_knots(pt.wind_speed_ms) if pt.wind_speed_ms is not None else None
                    for pt in points
                ],
                "kts",
                config.max_wind_speed_kts,
                "max",
            ),
            ("Wind Direction", [pt.wind_direction for pt in points], "deg", None, ""),
        ]

        try:
            fig, axes_arr = plt.subplots(  # type: ignore[reportUnknownMemberType]
                4, 1, figsize=(12, 10), sharex=True, squeeze=False
            )
            axes = axes_arr[:, 0]
            fig.suptitle(  # type: ignore[reportUnknownMemberType]
                "Cottesloe Beach Surf Forecast - "
                f"{day_forecast.forecast_date.strftime('%A, %d %B %Y')}",
                fontsize=14,
                fontweight="bold",
                y=0.98,
            )

            for ax, (label, values, unit, threshold, threshold_label) in zip(
                axes, panel_data, strict=True
            ):
                valid_times = [t for t, v in zip(times, values, strict=True) if v is not None]
                valid_values = [v for v in values if v is not None]

                if valid_times and valid_values:
                    ax.plot(
                        valid_times,
                        valid_values,
                        color="#2196F3",
                        linewidth=2,
                        marker="o",
                        markersize=3,
                    )

                ax.set_ylabel(f"{label} ({unit})", fontsize=9)
                ax.grid(True, alpha=0.3)

                if threshold is not None:
                    color = "#4CAF50" if threshold_label == "min" else "#F44336"
                    ax.axhline(
                        threshold,
                        color=color,
                        linestyle="--",
                        linewidth=1.5,
                        alpha=0.8,
                        label=f"{threshold_label} {threshold:.1f} {unit}",
                    )
                    ax.legend(fontsize=8, loc="upper right")

                for window in day_forecast.good_windows:
                    ax.axvspan(
                        window.start_time,
                        window.end_time,
                        alpha=0.15,
                        color="#4CAF50",
                        label="_nolegend_",
                    )

            axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
            axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=2))
            plt.setp(  # type: ignore[reportUnknownMemberType]
                axes[-1].xaxis.get_majorticklabels(), rotation=30, ha="right"
            )
            axes[-1].set_xlabel(f"Time ({config.timezone})", fontsize=9)

            fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))

            output_path = self._output_dir / f"surf_{day_forecast.forecast_date.isoformat()}.png"
            fig.savefig(output_path, dpi=150, bbox_inches="tight")  # type: ignore[reportUnknownMemberType]
            plt.close(fig)

            logger.info("Chart saved to {path}", path=output_path)
            return output_path

        except (OSError, ValueError, RuntimeError) as exc:
            raise ChartGenerationError(f"Chart generation failed: {exc}") from exc
