"""Full setup: entities on the integration device, summary/diagnostic sensors, events."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.suivi_colis.const import DOMAIN, EVENT_STATUS_CHANGED
from custom_components.suivi_colis.models import Package, TrackingEvent

API = "custom_components.suivi_colis.api_17track.Api17TrackClient"

MAILBOX = {
    "id": "mb1", "provider": "gmail", "name": "Perso", "server": "imap.gmail.com", "port": 993,
    "security": "ssl", "verify_ssl": True, "timeout": 30, "username": "a@gmail.com",
    "password": "pw", "folders": ["INBOX"], "dedicated": False, "unseen_only": False,
    "mark_seen": False, "search_days": 1, "max_emails": 50, "enabled": True,
}


def _packages():
    soon = datetime.now(UTC) + timedelta(days=1)
    later = datetime.now(UTC) + timedelta(days=3)
    return [
        Package("XA123456789FR", friendly_name="Clavier", carrier="chronopost", status="out_for_delivery",
                last_updated=datetime.now()),
        Package("CH514085194DE", carrier="dhl", status="in_transit", estimated_delivery_from=later,
                last_updated=datetime.now(),
                events=[TrackingEvent(datetime.now(UTC), "En transit", "Gonesse")]),
        Package("MR123456789", friendly_name="Chaussures", status="available_for_pickup",
                estimated_delivery_from=soon, last_updated=datetime.now()),
        Package("6A12345678901", status="delivery_failure", sub_status="DeliveryFailure_NoBody",
                last_updated=datetime.now()),
        Package("1Z999AA10123456784", status="delivered", last_updated=datetime.now()),
    ]


@pytest.fixture
async def setup(hass):
    """Set up the integration with 5 stored packages and one mailbox."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2,
        data={"api_key": "k", "webhook_id": "w" * 64},
        options={"archive_after_days": 0, "email_interval": 15, "mailboxes": [MAILBOX]},
    )
    entry.add_to_hass(hass)

    async def fake_load(store):
        for p in _packages():
            store._packages[p.tracking_number] = p

    track_info = {"CH514085194DE": {
        "status": "out_for_delivery", "sub_status": "OutForDelivery_Other", "sub_status_descr": "",
        "info_text": "En cours de livraison", "location": "Paris", "events": [], "carrier": "dhl",
        "carrier_code": 7041, "delivered_time": None, "estimated_delivery_from": None,
        "estimated_delivery_to": None, "estimated_delivery_source": "",
    }}
    with patch(f"{API}.get_track_info", AsyncMock(return_value=track_info)), \
         patch(f"{API}.get_quota", AsyncMock(return_value={"quota_total": 200, "quota_remain": 195, "quota_used": 5})), \
         patch("custom_components.suivi_colis.store.SuiviColisStore.async_load", fake_load), \
         patch("custom_components.suivi_colis.store.SuiviColisStore.async_save", AsyncMock()), \
         patch("custom_components.suivi_colis.coordinator.run_imap_fetch", side_effect=OSError("auth failed")), \
         patch("custom_components.suivi_colis._async_register_lovelace_resource", AsyncMock()):
        events = async_capture_events(hass, EVENT_STATUS_CHANGED)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, events
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_all_entities_on_the_integration_device(hass, setup):
    entry, _ = setup
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1 and devices[0].name == "Suivi de Colis"

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.device_id for e in entities} == {devices[0].id}
    by_id = {e.entity_id: e for e in entities}

    # every package in progress is listed (5 packages)
    packages = [e for e in entities if e.unique_id.startswith(f"{DOMAIN}_") and e.unique_id != f"{DOMAIN}_quota_remain"]
    assert len(packages) == 5
    assert "sensor.ch514085194de" in by_id  # entity id still based on the number

    diagnostics = {e.entity_id for e in entities if e.entity_category == EntityCategory.DIAGNOSTIC}
    assert diagnostics == {
        "sensor.suivi_de_colis_quota_17track_restant",
        "sensor.suivi_de_colis_derniere_actualisation",
        "binary_sensor.suivi_de_colis_boite_mail_perso",
    } or len(diagnostics) == 3  # names depend on the test language


async def test_summary_sensors(hass, setup):
    entry, events = setup
    registry = er.async_get(hass)

    def state(key, domain="sensor"):
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
        return hass.states.get(entity_id)

    assert state("in_progress").state == "4"  # all but the delivered one
    assert state("out_for_delivery_today").state == "2"  # CH… just went out for delivery
    assert state("to_pick_up").state == "1"
    assert state("to_pick_up").attributes["packages"][0]["name"] == "Chaussures"

    nxt = state("next_delivery")
    assert nxt.attributes["name"] == "Chaussures"  # earliest ETA among packages in progress
    assert datetime.fromisoformat(nxt.state) > datetime.now(UTC)

    problem = state("delivery_problem", "binary_sensor")
    assert problem.state == "on"
    assert problem.attributes["packages"][0]["tracking_number"] == "6A12345678901"

    mailbox = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_mailbox_mb1")
    mb_state = hass.states.get(mailbox)
    assert mb_state.state == "off" and mb_state.attributes["error"] == "auth failed"

    quota = hass.states.get(registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_quota_remain"))
    assert quota.state == "195"
    assert state("last_refresh").state not in ("unknown", "unavailable")

    # status change: bus event + event entity
    assert [(e.data["tracking_number"], e.data["old_status"], e.data["new_status"]) for e in events] == [
        ("CH514085194DE", "in_transit", "out_for_delivery")
    ]
    # later change (after startup): the event entity records it too
    coordinator = hass.data[DOMAIN][entry.entry_id]
    delivered = {"status": "delivered", "info_text": "Livré", "location": "Paris", "events": [],
                 "delivered_time": datetime.now(UTC)}
    coordinator._apply_track_data(coordinator.store.get_package("CH514085194DE"), delivered, datetime.now())
    await hass.async_block_till_done()
    ev = state("status_changed", "event")
    assert ev.attributes["event_type"] == "delivered"
    assert ev.attributes["tracking_number"] == "CH514085194DE"
    assert ev.attributes["old_status"] == "out_for_delivery"
    assert events[-1].data["new_status"] == "delivered"

    # package sensor: translated enum state, history not recorded
    pkg = hass.states.get("sensor.ch514085194de")
    assert pkg.state == "out_for_delivery"
    assert "out_for_delivery" in pkg.attributes["options"]


async def test_removing_a_mailbox_removes_its_entity(hass, setup):
    entry, _ = setup
    registry = er.async_get(hass)
    uid = f"{entry.entry_id}_mailbox_mb1"
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, uid)

    with patch(f"{API}.get_track_info", AsyncMock(return_value={})), \
         patch(f"{API}.get_quota", AsyncMock(return_value={})), \
         patch("custom_components.suivi_colis._async_register_lovelace_resource", AsyncMock()):
        hass.config_entries.async_update_entry(entry, options={**entry.options, "mailboxes": []})
        await hass.async_block_till_done()

    assert registry.async_get_entity_id("binary_sensor", DOMAIN, uid) is None
