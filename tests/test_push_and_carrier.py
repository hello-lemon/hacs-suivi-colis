"""Tests for the 17track webhook, the change_carrier service and the 2.2 migration."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suivi_colis import async_migrate_entry
from custom_components.suivi_colis.api_17track import Api17TrackClient, Api17TrackError
from custom_components.suivi_colis.const import DOMAIN
from custom_components.suivi_colis.coordinator import SuiviColisCoordinator
from custom_components.suivi_colis.models import Package
from custom_components.suivi_colis.webhook import (
    async_register_webhook,
    compute_signature,
    extract_items,
)

API_KEY = "secret-17track-key"
WEBHOOK_ID = "a" * 64

PUSH_UPDATED = {
    "event": "TRACKING_UPDATED",
    "data": {
        "number": "CH514085194DE",
        "carrier": 7041,
        "tag": None,
        "track_info": {
            "latest_status": {"status": "OutForDelivery", "sub_status": "OutForDelivery_Other"},
            "latest_event": {
                "time_iso": "2026-11-13T08:10:00+01:00",
                "description": "Out for delivery",
                "description_translation": {"lang": "fr", "description": "En cours de livraison"},
                "location": "Paris",
            },
            "tracking": {"providers": [{"provider": {"key": 7041, "name": "DHL Paket"}, "events": [
                {"time_iso": "2026-11-13T08:10:00+01:00", "description": "Out for delivery"}]}]},
        },
    },
}


# ------------------------------------------------------------------ helpers

def _store(*packages):
    pkgs = {p.tracking_number: p for p in packages}
    store = MagicMock()
    store.packages = pkgs
    store.active_packages = pkgs
    store.get_package = pkgs.get
    store.async_save = AsyncMock()
    return store


def _coordinator(hass, store, api=None):
    from homeassistant.config_entries import current_entry

    current_entry.set(MockConfigEntry(domain=DOMAIN))
    with patch("homeassistant.helpers.frame.report_usage"):
        return SuiviColisCoordinator(hass, api or Api17TrackClient(None, API_KEY), store, {}, {})


async def _post(hass_client_no_auth, body: str, sign: str | None):
    client = await hass_client_no_auth()
    headers = {"Content-Type": "application/json"}
    if sign is not None:
        headers["sign"] = sign
    return await client.post(f"/api/webhook/{WEBHOOK_ID}", data=body, headers=headers)


@pytest.fixture
async def webhook_ready(hass):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "webhook", {})


# ------------------------------------------------------------------ webhook

def test_signature_matches_doc_algorithm():
    # sha256(<body> + "/" + <key>) as hex, from the 17track documentation
    body = '{"event":"TRACKING_UPDATED","data":{"number":"RR123456789CN","carrier":3011,"tag":null}}'
    import hashlib

    assert compute_signature(body, "key") == hashlib.sha256(f"{body}/key".encode()).hexdigest()


def test_extract_items_both_documented_shapes():
    item = {"number": "N"}
    assert extract_items(item) == [item]
    assert extract_items({"accepted": [item]}) == [item]
    assert extract_items(None) == []


async def test_webhook_updates_package(hass, hass_client_no_auth, webhook_ready):
    pkg = Package("CH514085194DE", carrier="colissimo", status="in_transit")
    coord = _coordinator(hass, _store(pkg))
    coord.async_set_updated_data = MagicMock()
    async_register_webhook(hass, WEBHOOK_ID, API_KEY, coord)

    body = json.dumps(PUSH_UPDATED)
    resp = await _post(hass_client_no_auth, body, compute_signature(body, API_KEY))

    assert resp.status == 200
    assert pkg.status == "out_for_delivery"
    assert pkg.info_text == "En cours de livraison"
    assert pkg.carrier == "dhl" and pkg.carrier_code == 7041
    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize("sign", [None, "", "0" * 64])
async def test_webhook_rejects_bad_signature(hass, hass_client_no_auth, webhook_ready, sign):
    pkg = Package("CH514085194DE", status="in_transit")
    coord = _coordinator(hass, _store(pkg))
    async_register_webhook(hass, WEBHOOK_ID, API_KEY, coord)

    resp = await _post(hass_client_no_auth, json.dumps(PUSH_UPDATED), sign)

    assert resp.status == 401
    assert pkg.status == "in_transit"


async def test_webhook_tracking_stopped_and_test_event(hass, hass_client_no_auth, webhook_ready):
    pkg = Package("CH514085194DE", status="in_transit")
    coord = _coordinator(hass, _store(pkg))
    coord.async_set_updated_data = MagicMock()
    async_register_webhook(hass, WEBHOOK_ID, API_KEY, coord)

    stopped = json.dumps({"event": "TRACKING_STOPPED", "data": {"number": "CH514085194DE", "carrier": 7041}})
    assert (await _post(hass_client_no_auth, stopped, compute_signature(stopped, API_KEY))).status == 200
    assert pkg.tracking_stopped is True

    # dashboard "WebHook test" / unknown event / untracked number: 200, nothing changes
    for payload in ({"event": "TEST"}, {**PUSH_UPDATED, "data": {**PUSH_UPDATED["data"], "number": "OTHER"}}):
        body = json.dumps(payload)
        assert (await _post(hass_client_no_auth, body, compute_signature(body, API_KEY))).status == 200
    assert pkg.status == "in_transit"


# ------------------------------------------------------------------ change carrier

async def test_change_carrier_api_payload():
    c = Api17TrackClient(None, API_KEY)
    c._request = AsyncMock(return_value={"code": 0, "data": {"accepted": [{"number": "N", "carrier": 7041}], "rejected": []}})
    await c.change_carrier("N", 7041, old_code=6051)
    assert c._request.call_args.args[0].endswith("/v2.4/changecarrier")
    assert c._request.call_args.args[1] == [{"number": "N", "carrier_new": 7041, "carrier_old": 6051}]


async def test_change_carrier_api_rejection():
    c = Api17TrackClient(None, API_KEY)
    c._request = AsyncMock(return_value={"code": 0, "data": {"accepted": [], "rejected": [
        {"number": "N", "error": {"code": -18019807, "message": "The carrier change limit has been exceeded."}}]}})
    with pytest.raises(Api17TrackError, match="-18019807"):
        await c.change_carrier("N", 7041)


async def test_coordinator_change_carrier_resets_and_waits(hass):
    pkg = Package("CH514085194DE", carrier="colissimo", carrier_code=6051, status="in_transit",
                  info_text="old", sub_status="InTransit_Other")
    from datetime import datetime

    pkg.last_updated = datetime.now()
    api = MagicMock()
    api.change_carrier = AsyncMock()
    coord = _coordinator(hass, _store(pkg), api)
    coord.async_set_updated_data = MagicMock()
    coord._schedule_followup_refreshes = MagicMock()

    await coord.change_carrier("ch514085194de", 7041, "dhl")

    api.change_carrier.assert_awaited_once_with("CH514085194DE", 7041, 6051)
    assert (pkg.carrier, pkg.carrier_code, pkg.status) == ("dhl", 7041, "unknown")
    assert pkg.last_updated is None and pkg.info_text == "" and pkg.events == []
    coord._schedule_followup_refreshes.assert_called_once()


async def _setup_services(hass, coord):
    from custom_components.suivi_colis import _register_services

    _register_services(hass, coord)


async def test_change_carrier_service(hass):
    pkg = Package("CH514085194DE", carrier="colissimo")
    coord = MagicMock()
    coord.store = _store(pkg)
    coord.change_carrier = AsyncMock()
    await _setup_services(hass, coord)

    await hass.services.async_call(DOMAIN, "change_carrier",
                                   {"tracking_number": "ch514085194de", "carrier": "dhl_paket"}, blocking=True)
    coord.change_carrier.assert_awaited_with("CH514085194DE", 7041, "dhl")

    await hass.services.async_call(DOMAIN, "change_carrier",
                                   {"tracking_number": "CH514085194DE", "carrier_code": "100304"}, blocking=True)
    coord.change_carrier.assert_awaited_with("CH514085194DE", 100304, "")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "change_carrier", {"tracking_number": "CH514085194DE"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "change_carrier",
                                       {"tracking_number": "UNKNOWN", "carrier": "ups"}, blocking=True)

    coord.change_carrier = AsyncMock(side_effect=Api17TrackError("-18019807: limit"))
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "change_carrier",
                                       {"tracking_number": "CH514085194DE", "carrier": "ups"}, blocking=True)
    assert err.value.translation_key == "change_carrier_failed"
    assert "-18019807" in err.value.translation_placeholders["error"]


# ------------------------------------------------------------------ migration / storage

async def test_migration_2_1_adds_webhook_id(hass):
    entry = MockConfigEntry(domain=DOMAIN, version=2, minor_version=1,
                            data={"api_key": "k"}, options={"mailboxes": []})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 2
    first_id = entry.data["webhook_id"]
    assert len(first_id) == 64 and entry.options == {"mailboxes": []}

    # idempotent: an existing id is kept
    hass.config_entries.async_update_entry(entry, minor_version=1)
    assert await async_migrate_entry(hass, entry)
    assert entry.data["webhook_id"] == first_id


async def test_migration_refuses_downgrade(hass):
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={"api_key": "k"})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)


def test_package_from_1_0_storage():
    """A package saved by 1.0.x (no new fields) still loads."""
    old = {"tracking_number": "6A12345678901", "carrier": "colissimo", "status": "in_transit",
           "events": [], "added_at": "2026-11-01T10:00:00", "source": "manual", "archived": False}
    pkg = Package.from_dict(old)
    assert pkg.carrier_code is None and pkg.tracking_stopped is False
    assert Package.from_dict(pkg.to_dict()).to_dict() == pkg.to_dict()
