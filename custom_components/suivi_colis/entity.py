"""Shared entity helpers for Suivi de Colis."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SuiviColisCoordinator


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The single "Suivi de Colis" service device every entity belongs to.

    Grouping the entities under one device makes them all visible on the
    integration page (packages, summary and diagnostics).
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Suivi de Colis",
        manufacturer="17TRACK",
        model="Suivi de colis",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://api.17track.net/admin/dashboard",
    )


class SuiviColisEntity(CoordinatorEntity[SuiviColisCoordinator]):
    """Base entity attached to the integration device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SuiviColisCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(entry)
