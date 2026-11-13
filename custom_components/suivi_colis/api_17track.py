"""17track API v2.4 client (https://api.17track.net/en/doc)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import (
    API_17TRACK_CHANGECARRIER,
    API_17TRACK_GETQUOTA,
    API_17TRACK_GETTRACKINFO,
    API_17TRACK_LANGS,
    API_17TRACK_QUOTA_ERRORS,
    API_17TRACK_REGISTER,
    API_17TRACK_STOPTRACK,
    CARRIER_17TRACK_CODE,
)
from .models import PackageStatus, TrackingEvent

_LOGGER = logging.getLogger(__name__)

# Reverse mapping: 17track carrier code -> our carrier name
# Codes from https://res.17track.net/asset/carrier/info/apicarrier.all.json
# Unlisted codes fall back to the provider name (_normalize_carrier_name).
CARRIER_17TRACK_REVERSE = {
    100273: "chronopost",
    6051: "colissimo",
    100002: "ups",
    100398: "ups",  # UPS Mail Innovations
    190271: "cainiao",
    100027: "colisprive",
    100072: "dpd",
    101272: "gls",
    100304: "mondialrelay",
    100461: "relaiscolis",
    100241: "tnt",
    100003: "fedex",
    100854: "fedex",
    101020: "vintedgo",
    100001: "dhl",  # DHL Express
    7041: "dhl",  # DHL Paket
    101266: "dhl",
    100143: "amazon",  # Swiship (US)
    100572: "amazon",  # Swiship (FR)
    100308: "amazon",  # Amazon Shipping + MCF
    101001: "amazon",  # Amazon Shipping (FR)
    101392: "amazon",  # Amazon Shipping (US)
}


def _normalize_carrier_name(name: str) -> str:
    """Normalize a carrier name from 17track to our format."""
    name_lower = name.lower()
    for keyword, carrier in {
        "chronopost": "chronopost",
        "colissimo": "colissimo",
        "la poste": "colissimo",
        "laposte": "colissimo",
        "dhl": "dhl",
        "ups": "ups",
        "amazon": "amazon",
        "cainiao": "cainiao",
        "aliexpress": "cainiao",
        "yanwen": "cainiao",
        "colis priv": "colisprive",
    }.items():
        if keyword in name_lower:
            return carrier
    return name_lower


def to_17track_lang(language: str | None) -> str | None:
    """Map a Home Assistant language (e.g. "fr", "pt-BR", "zh-Hant") to 17track."""
    if not language:
        return None
    lang = language.lower().replace("_", "-")
    if lang in API_17TRACK_LANGS:
        return lang
    if lang.startswith("zh"):
        return "zh-hant" if lang in ("zh-tw", "zh-hk", "zh-mo") else "zh-hans"
    base = lang.split("-")[0]
    if base in ("nb", "nn"):
        return "no"
    return base if base in API_17TRACK_LANGS else None


def _parse_time(value: str | None) -> datetime | None:
    """Parse a 17track timestamp, always returning a timezone-aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Api17TrackError(Exception):
    """Base exception for 17track API errors."""


class Api17TrackRateLimited(Api17TrackError):
    """Rate limited by 17track."""


class Api17TrackQuotaExceeded(Api17TrackError):
    """Tracking quota exhausted or daily limit reached."""


class Api17TrackClient:
    """Client for 17track API v2.4."""

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, lang: str | None = None
    ) -> None:
        """Initialize. ``lang``: 17track language code for event translations."""
        self._session = session
        self._api_key = api_key
        self.lang = lang
        self._last_request_time: float = 0
        self._rate_lock = asyncio.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "17token": self._api_key,
            "Content-Type": "application/json",
        }

    async def _rate_limit(self) -> None:
        """Enforce 3 req/sec rate limit."""
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < 0.34:  # ~3 req/sec
                await asyncio.sleep(0.34 - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def _request(self, url: str, data: Any) -> dict[str, Any]:
        """Make a rate-limited request to 17track API."""
        await self._rate_limit()

        try:
            async with self._session.post(
                url, json=data, headers=self._headers
            ) as resp:
                if resp.status == 429:
                    raise Api17TrackRateLimited("Rate limited by 17track")
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as err:
            raise Api17TrackError(f"Request failed: {err}") from err

    async def validate_api_key(self) -> dict[str, Any]:
        """Validate API key by checking quota. Returns quota info."""
        result = await self._request(API_17TRACK_GETQUOTA, [])
        if result.get("code") != 0:
            raise Api17TrackError(
                f"Invalid API key: {result.get('message', 'Unknown error')}"
            )
        return result.get("data", {})

    async def register_package(self, tracking_number: str, carrier: str = "") -> bool:
        """Register a tracking number. Consumes 1 quota."""
        item: dict[str, Any] = {"number": tracking_number}
        carrier_code = CARRIER_17TRACK_CODE.get(carrier)
        if carrier_code:
            item["carrier"] = carrier_code
        if self.lang:
            # Carrier's official translation when available, original text
            # otherwise (default translation_mode: no extra quota)
            item["lang"] = self.lang

        result = await self._request(API_17TRACK_REGISTER, [item])
        code = result.get("code", -1)

        if code == 0:
            accepted = result.get("data", {}).get("accepted", [])
            rejected = result.get("data", {}).get("rejected", [])
            if accepted:
                _LOGGER.debug("Registered %s on 17track", tracking_number)
                return True
            if rejected:
                error = rejected[0].get("error", {})
                error_code = error.get("code", -1)
                # -18019901 = already registered, that's fine
                if error_code == -18019901:
                    _LOGGER.debug("%s already registered on 17track", tracking_number)
                    return True
                error_msg = error.get("message", "Unknown")
                # Quota exhausted / daily limit: retrying would fail the same way
                if error_code in API_17TRACK_QUOTA_ERRORS:
                    raise Api17TrackQuotaExceeded(
                        f"17track quota exhausted ({error_code}): {error_msg}"
                    )
                # If rejected with a carrier code, retry without it (let 17track auto-detect)
                if carrier_code:
                    _LOGGER.info(
                        "17track rejected %s with carrier %s (%s), retrying without carrier",
                        tracking_number, carrier, error_msg,
                    )
                    return await self.register_package(tracking_number, carrier="")
                _LOGGER.warning(
                    "17track rejected %s: %s", tracking_number, error_msg
                )
                return False

        _LOGGER.error("17track register error: %s", result)
        return False

    async def get_track_info(self, tracking_numbers: list[str]) -> dict[str, dict]:
        """Get tracking info for registered numbers. Free after register."""
        if not tracking_numbers:
            return {}

        output: dict[str, dict] = {}
        chunk_size = 40

        for i in range(0, len(tracking_numbers), chunk_size):
            chunk = tracking_numbers[i : i + chunk_size]
            items = [{"number": n} for n in chunk]

            try:
                result = await self._request(API_17TRACK_GETTRACKINFO, items)
            except Api17TrackRateLimited:
                raise
            except Api17TrackError as err:
                _LOGGER.error("17track gettrackinfo chunk error: %s", err)
                continue

            if result.get("code") != 0:
                _LOGGER.error("17track gettrackinfo error: %s", result)
                continue

            data = result.get("data", {})
            accepted = data.get("accepted", [])

            for item in accepted:
                number = item.get("number", "")
                output[number] = self.parse_tracked_item(item)

            # Typically -18019909 "no tracking information at this time": a newly
            # registered number 17track has not fetched from the carrier yet
            for item in data.get("rejected") or []:
                error = item.get("error") or {}
                _LOGGER.debug(
                    "17track has no data yet for %s (%s: %s)",
                    item.get("number"),
                    error.get("code"),
                    error.get("message"),
                )

        return output

    def parse_tracked_item(self, item: dict) -> dict:
        """Parse one gettrackinfo/webhook item ({number, carrier, track_info})."""
        data = self._parse_track_data(item.get("track_info") or {})
        code = item.get("carrier")
        data["carrier_code"] = code if isinstance(code, int) and code > 0 else None
        return data

    async def change_carrier(
        self, tracking_number: str, new_code: int, old_code: int | None = None
    ) -> None:
        """Change the carrier a number is tracked with. Raises Api17TrackError."""
        item: dict[str, Any] = {"number": tracking_number, "carrier_new": new_code}
        if old_code:
            item["carrier_old"] = old_code
        result = await self._request(API_17TRACK_CHANGECARRIER, [item])
        data = result.get("data") or {}
        if result.get("code") == 0 and data.get("accepted"):
            return
        rejected = data.get("rejected") or [{}]
        error = rejected[0].get("error") or {}
        raise Api17TrackError(
            f"{error.get('code', result.get('code'))}: "
            f"{error.get('message') or data.get('errors') or result}"
        )

    def _parse_track_data(self, track_info: dict) -> dict:
        """Parse 17track track_info into our format."""
        # Main status mapping (v2.x string statuses)
        status_map = {
            "NotFound": PackageStatus.NOT_FOUND,
            "InfoReceived": PackageStatus.INFO_RECEIVED,
            "InTransit": PackageStatus.IN_TRANSIT,
            "OutForDelivery": PackageStatus.OUT_FOR_DELIVERY,
            "AvailableForPickup": PackageStatus.AVAILABLE_FOR_PICKUP,
            "Delivered": PackageStatus.DELIVERED,
            "DeliveryFailure": PackageStatus.DELIVERY_FAILURE,
            "Exception": PackageStatus.EXCEPTION,
            "Expired": PackageStatus.EXPIRED,
        }

        # Parse latest status
        latest_status = track_info.get("latest_status") or {}
        status_str = latest_status.get("status", "NotFound")
        status = status_map.get(status_str, PackageStatus.UNKNOWN)

        # Parse events from all providers
        events: list[TrackingEvent] = []
        delivered_time: datetime | None = None
        tracking = track_info.get("tracking") or {}
        for provider in tracking.get("providers") or []:
            for event_data in provider.get("events") or []:
                timestamp = _parse_time(
                    event_data.get("time_iso") or event_data.get("time_utc")
                )
                if (
                    timestamp
                    and (event_data.get("sub_status") or "").startswith("Delivered")
                    and (delivered_time is None or timestamp > delivered_time)
                ):
                    delivered_time = timestamp
                location = event_data.get("location") or ""
                if not location:
                    addr = event_data.get("address") or {}
                    parts = [
                        addr.get("city") or "",
                        addr.get("state") or "",
                        addr.get("country") or "",
                    ]
                    location = ", ".join(p for p in parts if p)
                events.append(
                    TrackingEvent(
                        # Always timezone-aware: sorting a mix of naive and aware
                        # datetimes raises TypeError and fails the whole update.
                        timestamp=timestamp or datetime.now(UTC),
                        description=self._event_text(event_data),
                        location=location,
                    )
                )

        # Sort events newest first
        events.sort(key=lambda e: e.timestamp, reverse=True)

        # Delivery time: fall back on the "Delivered" milestone
        if delivered_time is None:
            for milestone in track_info.get("milestone") or []:
                if milestone.get("key_stage") == "Delivered":
                    delivered_time = _parse_time(
                        milestone.get("time_iso") or milestone.get("time_utc")
                    )

        # Latest event info
        latest_event = track_info.get("latest_event") or {}
        info_text = self._event_text(latest_event)
        if not info_text and events:
            info_text = events[0].description
        location = latest_event.get("location") or ""
        if not location and events:
            location = events[0].location

        # Estimated delivery date (17track model or the carrier itself)
        estimate = (track_info.get("time_metrics") or {}).get(
            "estimated_delivery_date"
        ) or {}

        # Carrier detection — try multiple fields
        carrier = ""

        # 1. From tracking providers (most reliable)
        for provider in tracking.get("providers", []):
            provider_info = provider.get("provider", {})
            carrier_code = provider_info.get("key")
            if carrier_code and not carrier:
                carrier = CARRIER_17TRACK_REVERSE.get(carrier_code, "")
            if not carrier:
                carrier_name = provider_info.get("name", "")
                if carrier_name:
                    carrier = _normalize_carrier_name(carrier_name)

        # 2. From misc_info.service_type (fallback)
        if not carrier:
            svc = (track_info.get("misc_info") or {}).get("service_type") or ""
            if svc:
                carrier = _normalize_carrier_name(svc)

        # 3. From misc_info.carrier_code
        if not carrier:
            code = (track_info.get("misc_info") or {}).get("carrier_code")
            if code:
                carrier = CARRIER_17TRACK_REVERSE.get(code, "")

        return {
            "status": status,
            "sub_status": latest_status.get("sub_status") or "",
            "sub_status_descr": latest_status.get("sub_status_descr") or "",
            "info_text": info_text,
            "location": location,
            "events": events,
            "carrier": carrier,
            "delivered_time": delivered_time,
            "estimated_delivery_from": _parse_time(estimate.get("from")),
            "estimated_delivery_to": _parse_time(estimate.get("to")),
            "estimated_delivery_source": estimate.get("source") or "",
        }

    @staticmethod
    def _event_text(event: dict) -> str:
        """Event description, translated by the carrier when available (v2.4)."""
        translation = event.get("description_translation") or {}
        return translation.get("description") or event.get("description") or ""

    async def stop_tracking(self, tracking_number: str) -> bool:
        """Stop tracking a number (archive)."""
        result = await self._request(
            API_17TRACK_STOPTRACK, [{"number": tracking_number}]
        )
        return result.get("code") == 0

    async def get_quota(self) -> dict[str, Any]:
        """Get current quota usage (quota_total, quota_used, quota_remain…)."""
        result = await self._request(API_17TRACK_GETQUOTA, [])
        if result.get("code") != 0:
            raise Api17TrackError(f"getquota error: {result}")
        return result.get("data") or {}
