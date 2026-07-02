"""
ical-merger: fetch multiple iCal URLs, merge them into one calendar, and
normalize event timestamps to UTC. Serves the result as text/calendar over HTTP.

Configured entirely via environment variables. See README.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, HTTPException, Response
from icalendar import Calendar, Event


# ---------------------------------------------------------------------------
# Configuration (read once at import; container is single-instance)
# ---------------------------------------------------------------------------

_CALENDAR_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def _require_urls() -> list[str]:
    raw = os.environ.get("URLS", "").strip()
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    if not urls:
        raise RuntimeError("URLS environment variable is required (comma-separated list)")
    return urls


def _load_calendar_id() -> str:
    raw = os.environ.get("CALENDAR_ID", "calendar").strip() or "calendar"
    if not all(c in _CALENDAR_ID_ALLOWED for c in raw):
        raise RuntimeError(
            f"CALENDAR_ID={raw!r} contains invalid characters "
            "(allowed: letters, digits, '-', '_')"
        )
    return raw


def _load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"SOURCE_TZ={name!r} is not a known IANA zone") from exc


URLS: list[str] = _require_urls()
CALENDAR_ID: str = _load_calendar_id()
SOURCE_TZ: ZoneInfo = _load_zone(os.environ.get("SOURCE_TZ", "Europe/Berlin"))
CALENDAR_NAME: str = os.environ.get("CALENDAR_NAME", "Merged Calendar")
CACHE_TTL: int = int(os.environ.get("CACHE_TTL", "900"))
HTTP_TIMEOUT: float = float(os.environ.get("HTTP_TIMEOUT", "30"))

_DT_KEYS = {"DTSTART", "DTEND", "RECURRENCE-ID"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ical-merger")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _to_utc_if_floating(value: datetime | date) -> datetime | date:
    """If value is a naive (floating) datetime, interpret it in SOURCE_TZ and
    convert to UTC. tz-aware datetimes and plain dates pass through unchanged.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    return value


def _normalize_event(src: Event) -> Event:
    """Copy an event, normalizing timestamps to UTC.

    Drops any standalone TZID property — some upstream sources emit it as a
    bare VEVENT property, which is not valid per RFC 5545 and is ignored by
    conformant parsers. Converting all floating datetimes to UTC ensures
    consistent display across all calendar clients.
    """
    dst = Event()
    for key in list(src.keys()):
        if key == "TZID":
            # Standalone TZID inside VEVENT is not valid per RFC 5545; drop it
            # to avoid confusing strict calendar clients.
            continue
        prop = src[key]
        if key in _DT_KEYS:
            dst.add(key, _to_utc_if_floating(prop.dt))
        else:
            dst.add(key, prop)
    return dst


def _build_output(calendars: list[Calendar]) -> str:
    """Merge a list of parsed source Calendars into one normalized calendar."""
    out = Calendar()
    out.add("prodid", f"-//{CALENDAR_NAME}//ical-merger//EN")
    out.add("version", "2.0")
    out.add("calscale", "GREGORIAN")
    out.add("method", "PUBLISH")
    out.add("x-wr-calname", CALENDAR_NAME)

    seen_uids: set[str] = set()
    for cal in calendars:
        for comp in cal.walk("VEVENT"):
            uid_prop = comp.get("UID")
            uid = str(uid_prop) if uid_prop is not None else ""
            if uid:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
            out.add_component(_normalize_event(comp))
    return out.to_ical().decode()


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

async def _fetch_one(url: str, client: httpx.AsyncClient) -> Optional[Calendar]:
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return Calendar.from_ical(r.text)
    except Exception as exc:  # noqa: BLE001 - we log and continue
        log.warning("source failed url=%s error=%s", url, exc)
        return None


async def _fetch_all() -> list[Calendar]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(_fetch_one(u, client) for u in URLS))
    return [c for c in results if c is not None]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _Cache:
    body: Optional[str] = None
    ts: float = 0.0
    lock: asyncio.Lock = asyncio.Lock()


_cache = _Cache()


async def _get_merged() -> str:
    now = time.monotonic()
    if _cache.body is not None and CACHE_TTL > 0 and (now - _cache.ts) < CACHE_TTL:
        return _cache.body

    async with _cache.lock:
        now = time.monotonic()
        if _cache.body is not None and CACHE_TTL > 0 and (now - _cache.ts) < CACHE_TTL:
            return _cache.body

        calendars = await _fetch_all()
        if not calendars:
            # All sources failed. Fall back to cached copy if we have one.
            if _cache.body is not None:
                log.warning("all sources failed; serving stale cache")
                return _cache.body
            raise RuntimeError("all sources failed and no cache available")

        if len(calendars) < len(URLS):
            log.warning(
                "partial merge: %d of %d sources succeeded",
                len(calendars), len(URLS),
            )

        body = _build_output(calendars)
        _cache.body = body
        _cache.ts = now
        return body


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

app = FastAPI(title="ical-merger", docs_url=None, redoc_url=None, openapi_url=None)


@app.get(f"/{CALENDAR_ID}.ics")
async def merged() -> Response:
    try:
        body = await _get_merged()
    except Exception as exc:  # noqa: BLE001
        log.error("failed to build response: %s", exc)
        raise HTTPException(status_code=503, detail="all sources failed")
    return Response(content=body, media_type="text/calendar; charset=utf-8")


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}
