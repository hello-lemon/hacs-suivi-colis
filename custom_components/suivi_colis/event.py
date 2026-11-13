"""Event entity: one event per package status change."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SuiviColisCoordinator
from .entity import SuiviColisEntity
from .models import PackageStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the status change event entity."""
    coordinator: SuiviColisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SuiviColisStatusEvent(coordinator, entry)])


class SuiviColisStatusEvent(SuiviColisEntity, EventEntity):
    """Fires with the new status as event type each time a package changes."""

    _attr_event_types = [s.value for s in PackageStatus]
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator: SuiviColisCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "status_changed")

    async def async_added_to_hass(self) -> None:
        """Listen to status changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_status_listener(self._handle_status_change)
        )

    @callback
    def _handle_status_change(self, data: dict) -> None:
        self._trigger_event(data["new_status"], data)
        self.async_write_ha_state()
