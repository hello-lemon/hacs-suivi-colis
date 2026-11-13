"""Binary sensors for Suivi de Colis: delivery problem, mailbox connectivity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MAILBOXES, DOMAIN, MB_ID, MB_NAME, MB_SERVER, MB_USERNAME
from .coordinator import SuiviColisCoordinator
from .entity import SuiviColisEntity
from .models import Package, PackageStatus

PROBLEM_STATUSES = {
    PackageStatus.DELIVERY_FAILURE,
    PackageStatus.EXCEPTION,
    PackageStatus.EXPIRED,
    PackageStatus.NOT_FOUND,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator: SuiviColisCoordinator = hass.data[DOMAIN][entry.entry_id]
    mailboxes = entry.options.get(CONF_MAILBOXES, [])

    entities: list[BinarySensorEntity] = [SuiviColisProblemSensor(coordinator, entry)]
    entities += [SuiviColisMailboxSensor(coordinator, entry, mb) for mb in mailboxes]
    async_add_entities(entities)

    # Remove the entities of mailboxes deleted from the options
    registry = er.async_get(hass)
    wanted = {f"{entry.entry_id}_mailbox_{mb[MB_ID]}" for mb in mailboxes}
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg.domain == "binary_sensor"
            and "_mailbox_" in reg.unique_id
            and reg.unique_id not in wanted
        ):
            registry.async_remove(reg.entity_id)


def is_problem(pkg: Package) -> bool:
    """Package needing attention (failed, returned, lost, no longer tracked…)."""
    if pkg.status == PackageStatus.DELIVERED:
        return False
    return pkg.status in PROBLEM_STATUSES or pkg.tracking_stopped


class SuiviColisProblemSensor(SuiviColisEntity, BinarySensorEntity):
    """On when at least one package has a delivery problem."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SuiviColisCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "delivery_problem")

    def _packages(self) -> list[Package]:
        return [p for p in self.coordinator.store.active_packages.values() if is_problem(p)]

    @property
    def is_on(self) -> bool:
        """At least one package needs attention."""
        return bool(self._packages())

    @property
    def extra_state_attributes(self) -> dict:
        """The packages with a problem."""
        return {
            "packages": [
                {
                    "name": p.display_name,
                    "tracking_number": p.tracking_number,
                    "status": p.status,
                    "sub_status": p.sub_status,
                    "info": p.info_text,
                    "tracking_stopped": p.tracking_stopped,
                }
                for p in self._packages()
            ]
        }


class SuiviColisMailboxSensor(SuiviColisEntity, BinarySensorEntity):
    """Connectivity of one IMAP mailbox (result of its last check)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: SuiviColisCoordinator, entry: ConfigEntry, mailbox: dict
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "mailbox")
        self._mailbox_id = mailbox[MB_ID]
        self._attr_unique_id = f"{entry.entry_id}_mailbox_{mailbox[MB_ID]}"
        self._attr_translation_placeholders = {
            "name": mailbox.get(MB_NAME) or mailbox.get(MB_USERNAME) or mailbox[MB_SERVER]
        }

    @property
    def _status(self) -> dict | None:
        return self.coordinator.mailbox_status.get(self._mailbox_id)

    @property
    def available(self) -> bool:
        """Known once the mailbox has been checked (disabled ones never are)."""
        return super().available and self._status is not None

    @property
    def is_on(self) -> bool | None:
        """Connected and logged in at the last check."""
        status = self._status
        return None if status is None else status.get("error") is None

    @property
    def extra_state_attributes(self) -> dict:
        """Last check, error and number of tracking numbers found."""
        status = self._status or {}
        last = status.get("last_check")
        return {
            "last_check": last.isoformat() if last else None,
            "error": status.get("error"),
            "found": status.get("found"),
        }
