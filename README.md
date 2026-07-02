# ical-merger

Tiny self-hosted HTTP service that fetches multiple iCal (`.ics`) feeds,
merges them into one calendar, and serves the result as `text/calendar`.

Purpose: fetch, merge, and normalize multiple iCal feeds into one
RFC 5545-compliant calendar.

## What it does

1. Fetches every URL in `URLS` in parallel.
2. Parses each source with the `icalendar` library.
3. For every `VEVENT`:
   - Converts floating (naive) `DTSTART`/`DTEND`/`RECURRENCE-ID` values from
     `SOURCE_TZ` to UTC, emitting them with a trailing `Z`.
   - Already-tz-aware and all-day (`DATE`) values pass through unchanged.
   - Every other property (`SUMMARY`, `DESCRIPTION`, `LOCATION`, `RRULE`,
     `UID`, `LAST-MODIFIED`, `DTSTAMP`, `CATEGORIES`, `X-*`, …) is preserved.
4. Deduplicates events by `UID` (first source wins).
5. Drops any source `VTIMEZONE` blocks — irrelevant once every timestamp is
   UTC.
6. Serves the merged calendar with an in-memory cache (default 15 min).

The output is RFC 5545-compliant and contains no timezone references, so
every client renders it identically regardless of local rules.

## Configuration

All configuration is via environment variables. One merged feed per
container; run multiple containers for multiple feeds.

| Variable        | Required | Default           | Description                                                          |
| --------------- | -------- | ----------------- | -------------------------------------------------------------------- |
| `URLS`          | yes      | —                 | Comma-separated list of source `.ics` URLs                           |
| `CALENDAR_ID`   | no       | `calendar`        | Path segment for the served feed (`/<CALENDAR_ID>.ics`). Allowed characters: letters, digits, `-`, `_` |
| `CALENDAR_NAME` | no       | `Merged Calendar` | Written to `PRODID` and `X-WR-CALNAME`                               |
| `SOURCE_TZ`     | no       | `Europe/Berlin`   | IANA zone to interpret floating `DTSTART`/`DTEND` values in          |
| `PORT`          | no       | `3000`            | HTTP listen port                                                     |
| `HOST`          | no       | `0.0.0.0`         | Bind address                                                         |
| `CACHE_TTL`     | no       | `900`             | Seconds to cache the merged output in memory (0 disables caching)    |
| `HTTP_TIMEOUT`  | no       | `30`              | Per-source fetch timeout in seconds                                  |

## Endpoints

- `GET /<CALENDAR_ID>.ics` — merged calendar as `text/calendar; charset=utf-8`
- `GET /healthz` — liveness probe (`{"ok": true}`)

## Failure behavior

- Individual source failures (network error, HTTP ≥ 400, parse error) are
  logged and skipped. The merge proceeds with the remaining sources.
- If all sources fail and there is no cached response, the service returns
  `503`. If all sources fail but a cached response exists, the stale copy
  is served (log warning emitted).

## Running

### Docker

```sh
docker run --rm -p 3000:3000 \
  -e URLS="https://example.com/a.ics,https://example.com/b.ics" \
  -e CALENDAR_ID="ff" \
  -e CALENDAR_NAME="My Merged Feed" \
  ghcr.io/mheidinger/ical-merger:latest
# Then: curl http://localhost:3000/ff.ics
```

### docker compose

See [`compose.example.yml`](compose.example.yml).

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
URLS="https://example.com/a.ics" CALENDAR_ID="ff" uvicorn app:app --reload
```

## Disclaimer

This project was fully created using Opus 4.7!
