# Ocean Uptime

A scheduled script that checks the 5-day marine weather forecast for Cottesloe Beach, Perth and sends a Slack notification with a forecast chart when surf conditions meet your thresholds.

Forecast data is sourced from [Stormglass.io](https://stormglass.io). The chart is uploaded directly to a Slack channel using the Slack Web API.

## How it works

Each run fetches hourly forecast data for the configured location, groups it into 3-hour windows during daylight hours, and checks each window against configurable thresholds (swell height, wind speed, wind direction). If any windows qualify, a 4-panel chart is generated and posted to Slack along with a summary message.

## Requirements

- [uv](https://docs.astral.sh/uv/) — used for dependency management and running the script
- A [Stormglass.io](https://stormglass.io) API key (free tier covers daily runs)
- A Slack app with a bot token, added to your target channel

## Slack app setup

1. Create a new app at [api.slack.com/apps](https://api.slack.com/apps)
2. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `chat:write`
   - `files:write`
   - `channels:join`
3. Install the app to your workspace and copy the **Bot User OAuth Token**
4. Find your channel ID (right-click a channel in Slack → View channel details → copy the ID at the bottom)

## Configuration

Copy the example environment file and fill in your values:

```
cp .env.example .env
```

**.env.example**:
```
STORMGLASS_API_KEY=your_stormglass_api_key_here
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token-here
SLACK_CHANNEL_ID=C0123456789
```

The surf thresholds and location are configured in `good_surf/config.py`. The defaults target Cottesloe Beach with a minimum swell height of 1.5 m, maximum wind of 15 knots, and an offshore wind window of 45–135 degrees (ENE to SE).

## Installation

```bash
# Clone the repo
git clone https://github.com/your-username/good-surf-notification.git
cd good-surf-notification

# Install dependencies
uv sync --locked
```

## Running

Required arguments are the location coordinates, name, and timezone. Surf quality thresholds are optional and have sensible defaults.

```bash

# Example
uv run surf.py \
  --lat -33.8731 \
  --lng 151.2773 \
  --location-name "Manly Beach, Sydney" \
  --timezone "Australia/Sydney" \
  --min-swell-height 1.2 \
  --max-wind-speed 12 \
  --offshore-wind-min 180 \
  --offshore-wind-max 270

# Force a notification regardless of conditions (useful for testing)
uv run surf.py --lat ... --lng ... --location-name "..." --timezone "..." --force
```

Full list of options:

| Argument | Required | Default | Description |
|---|---|---|---|
| `--lat` | yes | — | Latitude of the surf spot |
| `--lng` | yes | — | Longitude of the surf spot |
| `--location-name` | yes | — | Name shown in Slack notifications |
| `--timezone` | yes | — | IANA timezone (e.g. `Australia/Eucla`) |
| `--min-swell-height` | no | `1.5` | Minimum swell height in metres |
| `--max-wind-speed` | no | `15.0` | Maximum wind speed in knots |
| `--offshore-wind-min` | no | `45.0` | Minimum bearing (deg) for offshore wind |
| `--offshore-wind-max` | no | `135.0` | Maximum bearing (deg) for offshore wind |
| `--daylight-start` | no | `6` | Local hour to start analysing (0–23) |
| `--daylight-end` | no | `18` | Local hour to stop analysing (0–23) |
| `--forecast-days` | no | `5` | Days ahead to fetch |
| `--force` | no | — | Send alert regardless of conditions |

## Automated scheduling (systemd)

A systemd service and timer are included in the `systemd/` directory. The timer is configured to run at midnight UTC (8 AM AWST).

```bash
# Review and edit the deploy script for your paths, then run it
bash systemd/deploy-systemd.sh
```

See `systemd/secrets.env.template` for the format expected by the service's `EnvironmentFile`.

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run pyright
```
