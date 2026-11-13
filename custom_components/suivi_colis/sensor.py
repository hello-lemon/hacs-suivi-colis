"""Sensor platform for Suivi de Colis."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN, STATUS_ICONS
from .coordinator import SuiviColisCoordinator
from .entity import SuiviColisEntity, device_info
from .models import Package, PackageStatus

_LOGGER = logging.getLogger(__name__)

# Packages that are neither delivered nor archived
IN_PROGRESS = {s for s in PackageStatus if s != PackageStatus.DELIVERED}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suivi de Colis sensors from config entry."""
    coordinator: SuiviColisCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Account / summary sensors
    async_add_entities(
        [
            SuiviColisCountSensor(coordinator, entry, "in_progress", IN_PROGRESS, "mdi:package-variant"),
            SuiviColisCountSensor(
                coordinator, entry, "out_for_delivery_today",
                {PackageStatus.OUT_FOR_DELIVERY}, "mdi:truck-fast",
            ),
            SuiviColisCountSensor(
                coordinator, entry, "to_pick_up",
                {PackageStatus.AVAILABLE_FOR_PICKUP}, "mdi:store-marker",
            ),
            SuiviColisNextDeliverySensor(coordinator, entry),
            SuiviColisQuotaSensor(coordinator, entry),
            SuiviColisLastRefreshSensor(coordinator, entry),
        ]
    )

    # One sensor per package
    tracked: set[str] = set()

    @callback
    def _async_add_new_sensors() -> None:
        """Add sensors for newly discovered packages."""
        new_entities: list[SuiviColisSensor] = []
        for tracking_number, package in coordinator.store.active_packages.items():
            if tracking_number not in tracked:
                tracked.add(tracking_number)
                new_entities.append(SuiviColisSensor(coordinator, package, entry))
        # Forget archived packages
        tracked.intersection_update(coordinator.store.active_packages.keys())
        if new_entities:
            async_add_entities(new_entities)

    _async_add_new_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_sensors))


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _active(coordinator: SuiviColisCoordinator) -> list[Package]:
    return list(coordinator.store.active_packages.values())


# ---------------------------------------------------------------- packages


class SuiviColisSensor(CoordinatorEntity[SuiviColisCoordinator], SensorEntity):
    """Sensor for a single tracked package (state = status, translated in the UI)."""

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in PackageStatus]
    _attr_translation_key = "package"
    # The history can be long: keep it out of the recorder database
    _unrecorded_attributes = frozenset({"events"})

    def __init__(
        self,
        coordinator: SuiviColisCoordinator,
        package: Package,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._tracking_number = package.tracking_number
        self._attr_unique_id = f"{DOMAIN}_{package.tracking_number}"
        # Entity id stays based on the tracking number (as in 1.0.x), the
        # displayed name is the package name given by the user, if any
        self.entity_id = f"sensor.{slugify(package.tracking_number)}"
        self._attr_device_info = device_info(entry)

    @property
    def name(self) -> str:
        """Package name, or tracking number when it has none."""
        pkg = self._package
        return pkg.display_name if pkg else self._tracking_number

    @property
    def _package(self) -> Package | None:
        """Get the current package data."""
        return self.coordinator.store.get_package(self._tracking_number)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._package is not None and not self._package.archived

    @property
    def native_value(self) -> str | None:
        """Return the package status."""
        pkg = self._package
        if pkg is None:
            return None
        return pkg.status if pkg.status in self._attr_options else PackageStatus.UNKNOWN

    @property
    def icon(self) -> str:
        """Return icon based on status."""
        pkg = self._package
        status = pkg.status if pkg else "unknown"
        return STATUS_ICONS.get(status, "mdi:package-variant")

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes."""
        pkg = self._package
        if not pkg:
            return {}

        attrs = {
            "tracking_number": pkg.tracking_number,
            "carrier": pkg.carrier,
            "carrier_code": pkg.carrier_code,
            "tracking_stopped": pkg.tracking_stopped,
            # "friendly_name" is overwritten by Home Assistant with the entity
            # name: the package's own name goes in package_name
            "package_name": pkg.friendly_name,
            "info_text": pkg.info_text,
            "location": pkg.location,
            "source": pkg.source,
            "added_at": pkg.added_at.isoformat() if pkg.added_at else None,
            "last_updated": pkg.last_updated.isoformat() if pkg.last_updated else None,
            "delivered_at": pkg.delivered_at.isoformat() if pkg.delivered_at else None,
            "sub_status": pkg.sub_status,
            "sub_status_descr": pkg.sub_status_descr,
            "estimated_delivery_from": _iso(pkg.estimated_delivery_from),
            "estimated_delivery_to": _iso(pkg.estimated_delivery_to),
            "estimated_delivery_source": pkg.estimated_delivery_source,
            "events_count": len(pkg.events),
            "events": [
                {
                    "time": e.timestamp.isoformat(),
                    "description": e.description,
                    "location": e.location,
                }
                for e in pkg.events
            ],
        }

        if pkg.last_event:
            attrs["last_event"] = pkg.last_event.description
            attrs["last_event_time"] = pkg.last_event.timestamp.isoformat()
            attrs["last_event_location"] = pkg.last_event.location

        return attrs


# ---------------------------------------------------------------- summary


class SuiviColisCountSensor(SuiviColisEntity, SensorEntity):
    """Number of packages in some statuses (in progress, today, to pick up)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "colis"

    def __init__(
        self,
        coordinator: SuiviColisCoordinator,
        entry: ConfigEntry,
        key: str,
        statuses: set[str],
        icon: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, key)
        self._statuses = statuses
        self._attr_icon = icon

    def _packages(self) -> list[Package]:
        return [p for p in _active(self.coordinator) if p.status in self._statuses]

    @property
    def native_value(self) -> int:
        """Number of matching packages."""
        return len(self._packages())

    @property
    def extra_state_attributes(self) -> dict:
        """The matching packages, for templates and notifications."""
        return {
            "packages": [
                {
                    "name": p.display_name,
                    "tracking_number": p.tracking_number,
                    "carrier": p.carrier,
                    "status": p.status,
                }
                for p in self._packages()
            ]
        }


class SuiviColisNextDeliverySensor(SuiviColisEntity, SensorEntity):
    """Earliest estimated delivery among the packages in progress."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: SuiviColisCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "next_delivery")

    def _next(self) -> tuple[datetime, Package] | None:
        candidates = [
            (p.estimated_delivery_from or p.estimated_delivery_to, p)
            for p in _active(self.coordinator)
            if p.status != PackageStatus.DELIVERED
            and (p.estimated_delivery_from or p.estimated_delivery_to)
        ]
        return min(candidates, key=lambda c: c[0]) if candidates else None

    @property
    def native_value(self) -> datetime | None:
        """Date of the next expected delivery."""
        nxt = self._next()
        return dt_util.as_utc(nxt[0]) if nxt else None

    @property
    def extra_state_attributes(self) -> dict:
        """Which package is expected next."""
        nxt = self._next()
        if not nxt:
            return {}
        _, pkg = nxt
        return {
            "name": pkg.display_name,
            "tracking_number": pkg.tracking_number,
            "carrier": pkg.carrier,
            "estimated_delivery_to": _iso(pkg.estimated_delivery_to),
        }


# ---------------------------------------------------------------- diagnostics


class SuiviColisQuotaSensor(SuiviColisEntity, SensorEntity):
    """Remaining 17track tracking quota (1 credit per new tracking number)."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "suivis"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SuiviColisCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "quota_remain")
        # Keep the unique id of 1.1.0 so the existing entity is reused
        self._attr_unique_id = f"{DOMAIN}_quota_remain"

    @property
    def available(self) -> bool:
        """Available once the quota has been fetched."""
        return super().available and self.coordinator.quota is not None

    @property
    def native_value(self) -> int | None:
        """Return the remaining quota."""
        return (self.coordinator.quota or {}).get("quota_remain")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the other quota counters."""
        quota = self.coordinator.quota or {}
        return {
            key: quota.get(key)
            for key in ("quota_total", "quota_used", "today_used", "max_track_daily")
            if key in quota
        }


class SuiviColisLastRefreshSensor(SuiviColisEntity, SensorEntity):
    """Last successful update from 17track (polling or webhook)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:update"

    def __init__(self, coordinator: SuiviColisCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "last_refresh")

    @property
    def native_value(self) -> datetime | None:
        """Time of the last successful refresh."""
        return self.coordinator.last_refresh

    @property
    def extra_state_attributes(self) -> dict:
        """Last webhook notification and email check."""
        return {
            "last_push": _iso(self.coordinator.last_push),
            "last_email_check": _iso(self.coordinator.last_email_check),
        }
