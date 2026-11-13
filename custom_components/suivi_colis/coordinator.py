"""Data update coordinator for Suivi de Colis."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api_17track import Api17TrackClient, Api17TrackError, Api17TrackRateLimited
from .carrier_detect import detect_carrier_from_number
from .const import (
    CONF_ARCHIVE_AFTER_DAYS,
    CONF_EMAIL_INTERVAL,
    CONF_MAILBOXES,
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_EMAIL_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_STATUS_CHANGED,
    MB_ENABLED,
    MB_ID,
    MB_NAME,
    MB_SERVER,
)
from .email_parser import ExtractedPackage, run_imap_fetch
from .models import Package, PackageSource, PackageStatus
from .store import SuiviColisStore

_LOGGER = logging.getLogger(__name__)

# Seconds after a registration at which to poll 17track again
FOLLOWUP_REFRESH_DELAYS = (60, 180, 600)


class SuiviColisCoordinator(DataUpdateCoordinator[dict[str, Package]]):
    """Coordinate updates for all tracked packages."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: Api17TrackClient,
        store: SuiviColisStore,
        config: dict,
        options: dict,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL),
        )
        self.api_client = api_client
        self.store = store
        self.config = config
        self.options = options
        self._last_email_check: datetime | None = None
        # Per-mailbox status of the last check: {id: {"last_check", "error", "found"}}
        self.mailbox_status: dict[str, dict] = {}
        # Last /getquota answer (quota_total, quota_used, quota_remain, ...)
        self.quota: dict | None = None
        # Last successful 17track refresh (polling) and webhook notification
        self.last_refresh: datetime | None = None
        self.last_push: datetime | None = None
        self._status_listeners: list[Callable[[dict], None]] = []
        self._followup_unsubs: list = []

    @property
    def mailboxes(self) -> list[dict]:
        """Return the enabled mailboxes."""
        return [
            mb
            for mb in self.options.get(CONF_MAILBOXES, [])
            if mb.get(MB_ENABLED, True)
        ]

    @property
    def imap_configured(self) -> bool:
        """Return True if at least one mailbox is enabled."""
        return bool(self.mailboxes)

    @property
    def archive_after_days(self) -> int:
        """Return auto-archive delay in days."""
        return self.options.get(
            CONF_ARCHIVE_AFTER_DAYS, DEFAULT_ARCHIVE_AFTER_DAYS
        )

    @property
    def email_interval_minutes(self) -> int:
        """Return email check interval in minutes."""
        return self.options.get(CONF_EMAIL_INTERVAL, DEFAULT_EMAIL_INTERVAL)

    async def _async_update_data(self) -> dict[str, Package]:
        """Fetch latest data from 17track + emails."""
        # 1. Check emails if configured and interval elapsed
        if self.imap_configured and self._should_check_email():
            await self._check_emails()
            self._last_email_check = datetime.now()

        # 2. Update active packages via 17track
        await self._update_tracking()

        # 2b. Quota left on the 17track account
        await self._update_quota()

        # 3. Auto-remove delivered
        await self._auto_remove_delivered()

        self.last_refresh = dt_util.utcnow()

        # 4. Save to storage
        await self.store.async_save()

        return self.store.active_packages

    async def _update_tracking(self) -> None:
        """Update all active packages via 17track."""
        active = {
            k: v
            for k, v in self.store.active_packages.items()
            if v.status != PackageStatus.DELIVERED or v.last_updated is None
        }

        if not active:
            return

        numbers = list(active.keys())
        try:
            results = await self.api_client.get_track_info(numbers)
        except Api17TrackRateLimited:
            _LOGGER.warning("17track rate limited, will retry next cycle")
            return
        except Api17TrackError as err:
            _LOGGER.error("17track API error: %s", err)
            return

        now = datetime.now()
        for number, data in results.items():
            pkg = self.store.get_package(number)
            if pkg:
                self._apply_track_data(pkg, data, now)

    def _apply_track_data(self, pkg: Package, data: dict, now: datetime) -> None:
        """Apply parsed 17track data (polling or webhook) to a package."""
        old_status = pkg.status
        pkg.tracking_stopped = False
        if data.get("carrier_code"):
            pkg.carrier_code = data["carrier_code"]
        pkg.status = data["status"]
        pkg.sub_status = data.get("sub_status", "")
        pkg.sub_status_descr = data.get("sub_status_descr", "")
        pkg.estimated_delivery_from = data.get("estimated_delivery_from")
        pkg.estimated_delivery_to = data.get("estimated_delivery_to")
        pkg.estimated_delivery_source = data.get("estimated_delivery_source", "")
        pkg.info_text = data["info_text"]
        pkg.location = data["location"]
        pkg.events = data["events"]
        pkg.last_updated = now
        # Update carrier from 17track (always trust API over local detection)
        if data.get("carrier"):
            pkg.carrier = data["carrier"]

        if pkg.status != old_status:
            self._notify_status_change(pkg, old_status)

        if pkg.status == PackageStatus.DELIVERED and (
            old_status != PackageStatus.DELIVERED or pkg.delivered_at is None
        ):
            # Real delivery time from the carrier, in local naive time like
            # the other timestamps of the package; detection time otherwise
            delivered = data.get("delivered_time")
            pkg.delivered_at = (
                dt_util.as_local(delivered).replace(tzinfo=None)
                if delivered
                else now
            )
            if old_status != PackageStatus.DELIVERED:
                _LOGGER.info("Package %s delivered!", pkg.display_name)

    @property
    def last_email_check(self) -> datetime | None:
        """Last time the mailboxes were checked (local time)."""
        return self._last_email_check

    @callback
    def async_add_status_listener(
        self, listener: Callable[[dict], None]
    ) -> CALLBACK_TYPE:
        """Call ``listener`` with the event data on every status change."""
        self._status_listeners.append(listener)
        return lambda: self._status_listeners.remove(listener)

    def _notify_status_change(self, pkg: Package, old_status: str) -> None:
        """Fire suivi_colis_status_changed (bus event + event entity)."""
        data = {
            "tracking_number": pkg.tracking_number,
            "name": pkg.display_name,
            "carrier": pkg.carrier,
            "old_status": old_status,
            "new_status": pkg.status,
            "sub_status": pkg.sub_status,
            "info": pkg.info_text,
            "location": pkg.location,
        }
        self.hass.bus.async_fire(EVENT_STATUS_CHANGED, data)
        for listener in list(self._status_listeners):
            listener(data)

    async def async_handle_push(self, event: str, items: list[dict]) -> int:
        """Apply a 17track webhook notification. Returns the packages updated."""
        now = datetime.now()
        updated = 0
        for item in items:
            number = str(item.get("number") or "").strip().upper()
            pkg = self.store.get_package(number)
            if pkg is None:
                _LOGGER.debug("17track push for untracked number %s ignored", number)
                continue
            if event == "TRACKING_STOPPED":
                pkg.tracking_stopped = True
                _LOGGER.info("17track stopped tracking %s", pkg.display_name)
            else:
                self._apply_track_data(pkg, self.api_client.parse_tracked_item(item), now)
            updated += 1
        if updated:
            self.last_push = dt_util.utcnow()
            await self.store.async_save()
            self.async_set_updated_data(self.store.active_packages)
        return updated

    async def change_carrier(
        self, tracking_number: str, new_code: int, carrier_name: str
    ) -> None:
        """Track a package with another carrier. Raises Api17TrackError."""
        tracking_number = tracking_number.strip().upper()
        pkg = self.store.get_package(tracking_number)
        if pkg is None:
            raise Api17TrackError(f"Package {tracking_number} is not tracked")
        await self.api_client.change_carrier(tracking_number, new_code, pkg.carrier_code)
        # 17track fetches the new carrier from scratch: wait for fresh data
        pkg.carrier = carrier_name
        pkg.carrier_code = new_code
        pkg.status = PackageStatus.UNKNOWN
        pkg.sub_status = ""
        pkg.sub_status_descr = ""
        pkg.info_text = ""
        pkg.location = ""
        pkg.events = []
        pkg.last_updated = None
        pkg.estimated_delivery_from = None
        pkg.estimated_delivery_to = None
        pkg.estimated_delivery_source = ""
        await self.store.async_save()
        self.async_set_updated_data(self.store.active_packages)
        self._schedule_followup_refreshes()

    async def _update_quota(self) -> None:
        """Refresh the 17track quota (free call)."""
        try:
            self.quota = await self.api_client.get_quota()
        except Api17TrackError as err:
            _LOGGER.debug("Could not fetch 17track quota: %s", err)

    async def _check_emails(self) -> None:
        """Check every enabled mailbox for new tracking numbers."""
        known = set(self.store.packages.keys())
        extracted: list[ExtractedPackage] = []

        for mailbox in self.mailboxes:
            label = mailbox.get(MB_NAME) or mailbox.get(MB_SERVER)
            status = {"last_check": datetime.now(), "error": None, "found": 0}
            try:
                # `known` is shared so a number found in two mailboxes is added once
                found: list[ExtractedPackage] = await self.hass.async_add_executor_job(
                    run_imap_fetch, mailbox, known
                )
            except Exception as err:
                _LOGGER.error("Email check failed for mailbox %s: %s", label, err)
                status["error"] = str(err) or type(err).__name__
            else:
                status["found"] = len(found)
                extracted.extend(found)
            self.mailbox_status[mailbox[MB_ID]] = status

        for item in extracted:
            await self.add_package(
                tracking_number=item.tracking_number,
                carrier=item.carrier,
                friendly_name=item.friendly_name,
                source=PackageSource.EMAIL,
            )

    def _should_check_email(self) -> bool:
        """Check if enough time has passed for email check."""
        if self._last_email_check is None:
            return True
        elapsed = datetime.now() - self._last_email_check
        return elapsed >= timedelta(minutes=self.email_interval_minutes)

    async def _auto_remove_delivered(self) -> None:
        """Remove delivered packages after configured delay."""
        if self.archive_after_days <= 0:
            return

        cutoff = datetime.now() - timedelta(days=self.archive_after_days)
        to_remove: list[str] = []
        for pkg in list(self.store.active_packages.values()):
            if (
                pkg.status == PackageStatus.DELIVERED
                and pkg.delivered_at
                and pkg.delivered_at < cutoff
            ):
                to_remove.append(pkg.tracking_number)

        for number in to_remove:
            self.store.remove_package(number)
            self._remove_entity(number)
            try:
                await self.api_client.stop_tracking(number)
            except Api17TrackError as err:
                _LOGGER.warning("Failed to stop 17track for %s: %s", number, err)
            _LOGGER.info("Auto-removed delivered package %s", number)

    async def add_package(
        self,
        tracking_number: str,
        carrier: str = "",
        friendly_name: str = "",
        source: str = PackageSource.MANUAL,
    ) -> bool:
        """Add a new package and register with 17track."""
        tracking_number = tracking_number.strip().upper()

        if self.store.has_package(tracking_number):
            _LOGGER.warning("Package %s already tracked", tracking_number)
            return False

        # Auto-detect carrier if not specified
        if not carrier or carrier == "unknown":
            carrier = detect_carrier_from_number(tracking_number)

        # Register with 17track
        try:
            registered = await self.api_client.register_package(
                tracking_number, carrier
            )
            if not registered:
                _LOGGER.error("Failed to register %s with 17track", tracking_number)
                return False
        except Api17TrackError as err:
            _LOGGER.error("17track register error for %s: %s", tracking_number, err)
            return False

        pkg = Package(
            tracking_number=tracking_number,
            carrier=carrier,
            friendly_name=friendly_name,
            source=source,
        )
        self.store.add_package(pkg)
        await self.store.async_save()

        # Trigger an immediate refresh
        await self.async_request_refresh()
        self._schedule_followup_refreshes()
        return True

    def _schedule_followup_refreshes(self) -> None:
        """Poll again shortly after a registration.

        17track fetches a new number from the carrier within seconds to several
        minutes, so the immediate refresh usually comes too early; without this
        the package would stay "unknown" until the next 30-minute cycle.
        """
        for unsub in self._followup_unsubs:
            unsub()
        self._followup_unsubs = [
            async_call_later(self.hass, delay, self._async_followup_refresh)
            for delay in FOLLOWUP_REFRESH_DELAYS
        ]

    async def _async_followup_refresh(self, _now: datetime) -> None:
        """Refresh only while a package still waits for its first data."""
        if any(p.last_updated is None for p in self.store.active_packages.values()):
            await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel pending follow-up refreshes."""
        for unsub in self._followup_unsubs:
            unsub()
        self._followup_unsubs = []
        await super().async_shutdown()

    def _remove_entity(self, tracking_number: str) -> None:
        """Remove the entity from HA entity registry."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_{tracking_number}"
        )
        if entity_id:
            registry.async_remove(entity_id)

    async def remove_package(self, tracking_number: str) -> bool:
        """Remove a package and stop tracking on 17track."""
        tracking_number = tracking_number.strip().upper()

        pkg = self.store.remove_package(tracking_number)
        if not pkg:
            return False

        try:
            await self.api_client.stop_tracking(tracking_number)
        except Api17TrackError as err:
            _LOGGER.warning("Failed to stop 17track for %s: %s", tracking_number, err)

        self._remove_entity(tracking_number)
        await self.store.async_save()
        await self.async_request_refresh()
        return True

    async def archive_delivered(self) -> int:
        """Archive all delivered packages. Returns count."""
        count = 0
        for pkg in list(self.store.active_packages.values()):
            if pkg.status == PackageStatus.DELIVERED:
                pkg.archived = True
                count += 1
        if count:
            await self.store.async_save()
            await self.async_request_refresh()
        return count
