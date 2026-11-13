"""17track client checks against the documented v2.x response shapes."""

from unittest.mock import AsyncMock

import pytest

from custom_components.suivi_colis.api_17track import (
    Api17TrackClient,
    Api17TrackQuotaExceeded,
)
from custom_components.suivi_colis.const import CARRIER_17TRACK_CODE


def _client(responses):
    c = Api17TrackClient(session=None, api_key="k")
    c._request = AsyncMock(side_effect=responses)
    return c


def _rejected(code):
    return {"code": 0, "data": {"accepted": [], "rejected": [
        {"number": "X", "error": {"code": code, "message": "m"}}]}}


async def test_register_sends_doc_carrier_codes():
    c = _client([{"code": 0, "data": {"accepted": [{"number": "XA123456789FR", "carrier": 100273}], "rejected": []}}])
    assert await c.register_package("XA123456789FR", "chronopost")
    assert c._request.call_args.args[1] == [{"number": "XA123456789FR", "carrier": 100273}]


@pytest.mark.parametrize("carrier", ["dhl", "amazon", "unknown"])
async def test_ambiguous_carriers_use_auto_detection(carrier):
    c = _client([{"code": 0, "data": {"accepted": [{"number": "N"}], "rejected": []}}])
    await c.register_package("JJD000000000000000001", carrier)
    assert c._request.call_args.args[1] == [{"number": "JJD000000000000000001"}]


def test_no_fedex_code_for_dhl():
    # 100003 is FedEx: DHL must never be registered with it (auto-detection instead)
    assert CARRIER_17TRACK_CODE.get("dhl") is None
    assert CARRIER_17TRACK_CODE["fedex"] == 100003


@pytest.mark.parametrize("code", [-18019907, -18019908])
async def test_quota_errors_raise_without_retry(code):
    c = _client([_rejected(code)])
    with pytest.raises(Api17TrackQuotaExceeded):
        await c.register_package("6A12345678901", "colissimo")
    assert c._request.await_count == 1


async def test_carrier_rejection_retries_with_autodetect():
    c = _client([_rejected(-18019910), {"code": 0, "data": {"accepted": [{"number": "N"}], "rejected": []}}])
    assert await c.register_package("6A12345678901", "colissimo")
    assert c._request.call_args_list[1].args[1] == [{"number": "6A12345678901"}]


async def test_already_registered_is_ok():
    assert await _client([_rejected(-18019901)]).register_package("N", "")


async def test_getquota_body_is_empty_array():
    c = _client([{"code": 0, "data": {"quota_total": 200, "quota_remain": 198}}])
    assert (await c.validate_api_key())["quota_remain"] == 198
    assert c._request.call_args.args[1] == []


def test_parse_doc_track_info_mixed_timestamps():
    track_info = {
        "latest_status": {"status": "Delivered", "sub_status": "Delivered_Other"},
        "latest_event": {"description": "Livré", "location": "Paris"},
        "tracking": {"providers": [
            {"provider": {"key": 100273, "name": "Chronopost"}, "events": [
                {"time_iso": "2026-11-10T08:00:00+01:00", "description": "Livré", "location": "Paris"},
                {"time_iso": "2026-11-09T18:00:00", "description": "En cours", "address": {"city": "Lyon", "country": "FR"}},
                {"time_utc": "2026-11-08T10:00:00Z", "description": "Pris en charge"},
            ]},
        ]},
    }
    out = Api17TrackClient(None, "k")._parse_track_data(track_info)
    assert out["status"] == "delivered"
    assert out["carrier"] == "chronopost"
    assert [e.description for e in out["events"]] == ["Livré", "En cours", "Pris en charge"]
    assert out["events"][1].location == "Lyon, FR"
    assert all(e.timestamp.tzinfo for e in out["events"])


def test_fedex_code_not_shown_as_dhl():
    out = Api17TrackClient(None, "k")._parse_track_data(
        {"tracking": {"providers": [{"provider": {"key": 100003, "name": "FedEx"}, "events": []}]}}
    )
    assert out["carrier"] == "fedex"
