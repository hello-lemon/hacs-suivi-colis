"""Tests for the multi-mailbox config/options flow, migration and coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suivi_colis import async_migrate_entry
from custom_components.suivi_colis.const import CONF_MAILBOXES, DOMAIN
from custom_components.suivi_colis.coordinator import SuiviColisCoordinator
from custom_components.suivi_colis.email_parser import ExtractedPackage, ImapCheckResult

CHECK = "custom_components.suivi_colis.email_parser.check_imap_connection"


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.suivi_colis.async_setup_entry", return_value=True), patch(
        "custom_components.suivi_colis.async_setup", return_value=True
    ):
        yield


@pytest.fixture
def api_ok():
    with patch(
        "custom_components.suivi_colis.config_flow.Api17TrackClient.validate_api_key",
        new=AsyncMock(return_value=True),
    ):
        yield


async def _add_mailbox(hass, flow_id, flow_mgr, user, provider="gmail", folders=("INBOX",)):
    res = await flow_mgr.async_configure(flow_id, {"next_step_id": "mailbox_provider"})
    assert res["step_id"] == "mailbox_provider"
    res = await flow_mgr.async_configure(flow_id, {"provider": provider})
    assert res["type"] == FlowResultType.FORM and res["step_id"] == "mailbox_connection"
    with patch(CHECK, return_value=ImapCheckResult(folders=["INBOX", "Livraisons"])) as chk:
        res = await flow_mgr.async_configure(
            flow_id,
            {
                "server": "imap.gmail.com",
                "security": "ssl",
                "verify_ssl": True,
                "username": user,
                "password": "secret",
                "timeout": 30,
            },
        )
    assert res["step_id"] == "mailbox_settings", res.get("errors")
    # port left empty -> standard port of the security mode
    assert chk.call_args.args[1] == 993
    res = await flow_mgr.async_configure(
        flow_id,
        {
            "folders": list(folders),
            "dedicated": False,
            "search_days": 2,
            "max_emails": 50,
            "unseen_only": False,
            "mark_seen": False,
            "enabled": True,
        },
    )
    return res


async def test_config_flow_two_mailboxes(hass, api_ok):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"api_key": "k"})
    assert res["type"] == FlowResultType.MENU and res["step_id"] == "mailboxes"
    fid = res["flow_id"]

    res = await _add_mailbox(hass, fid, hass.config_entries.flow, "a@gmail.com")
    assert res["type"] == FlowResultType.MENU
    assert "a@gmail.com" in res["description_placeholders"]["mailboxes"]
    res = await _add_mailbox(
        hass, fid, hass.config_entries.flow, "b@gmail.com", folders=("INBOX", "Livraisons")
    )
    assert res["type"] == FlowResultType.MENU

    res = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["api_key"] == "k"
    assert len(res["data"]["webhook_id"]) == 64  # created with the entry (v2.2)
    assert res["result"].minor_version == 2
    mbs = res["options"][CONF_MAILBOXES]
    assert [m["username"] for m in mbs] == ["a@gmail.com", "b@gmail.com"]
    assert mbs[1]["folders"] == ["INBOX", "Livraisons"]
    assert mbs[0]["port"] == 993 and mbs[0]["password"] == "secret"
    assert mbs[0]["id"] != mbs[1]["id"]
    assert res["result"].version == 2


async def test_config_flow_skip_email(hass, api_ok):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"api_key": "k"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"next_step_id": "finish"})
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["options"][CONF_MAILBOXES] == []


async def test_connection_errors_and_duplicate(hass, api_ok):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"api_key": "k"})
    fid = res["flow_id"]
    await _add_mailbox(hass, fid, hass.config_entries.flow, "a@gmail.com")

    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "mailbox_provider"})
    await hass.config_entries.flow.async_configure(fid, {"provider": "custom"})
    form = {
        "server": "mail.example.com",
        "security": "starttls",
        "verify_ssl": True,
        "username": "x",
        "password": "bad",
        "timeout": 30,
    }
    with patch(CHECK, return_value=ImapCheckResult(error="imap_auth_failed", detail="NO")) as chk:
        res = await hass.config_entries.flow.async_configure(fid, form)
    assert res["errors"] == {"base": "imap_auth_failed"}
    assert res["description_placeholders"]["error_detail"] == "NO"
    assert chk.call_args.args[1] == 143  # STARTTLS default port
    assert chk.call_args.args[4] == "starttls"

    # same server + user as an existing mailbox (case-insensitive)
    res = await hass.config_entries.flow.async_configure(
        fid, {**form, "server": "IMAP.gmail.com", "username": "A@gmail.com"}
    )
    assert res["errors"] == {"base": "mailbox_exists"}


def _entry(hass, mailboxes):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"api_key": "k"},
        options={"archive_after_days": 2, "email_interval": 15, CONF_MAILBOXES: mailboxes},
    )
    entry.add_to_hass(hass)
    return entry


MB = {
    "id": "abc",
    "provider": "gmail",
    "name": "Perso",
    "server": "imap.gmail.com",
    "port": 993,
    "security": "ssl",
    "verify_ssl": True,
    "timeout": 30,
    "username": "a@gmail.com",
    "password": "old-secret",
    "folders": ["INBOX"],
    "dedicated": False,
    "unseen_only": False,
    "mark_seen": False,
    "search_days": 1,
    "max_emails": 50,
    "enabled": True,
}


async def test_options_edit_keeps_password(hass):
    entry = _entry(hass, [dict(MB)])
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.MENU
    assert set(res["menu_options"]) == {"general", "mailbox_provider", "mailbox_edit", "mailbox_remove", "webhook"}
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"next_step_id": "mailbox_edit"})
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"id": "abc"})
    assert res["step_id"] == "mailbox_connection"
    with patch(CHECK, return_value=ImapCheckResult(folders=["INBOX", "Archive"])) as chk:
        res = await hass.config_entries.options.async_configure(
            res["flow_id"],
            {
                "name": "Perso",
                "server": "imap.gmail.com",
                "security": "ssl",
                "port": 993,
                "verify_ssl": False,
                "username": "a@gmail.com",
                "timeout": 60,
            },
        )
    assert chk.call_args.args[3] == "old-secret"
    assert res["step_id"] == "mailbox_settings"
    res = await hass.config_entries.options.async_configure(
        res["flow_id"],
        {
            "folders": ["INBOX", "Archive"],
            "dedicated": True,
            "search_days": 3,
            "max_emails": 0,
            "unseen_only": True,
            "mark_seen": True,
            "enabled": False,
        },
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    mbs = entry.options[CONF_MAILBOXES]
    assert len(mbs) == 1
    mb = mbs[0]
    assert mb["id"] == "abc" and mb["password"] == "old-secret"
    assert mb["verify_ssl"] is False and mb["timeout"] == 60
    assert mb["folders"] == ["INBOX", "Archive"] and mb["enabled"] is False
    assert entry.options["archive_after_days"] == 2  # untouched


async def test_options_remove_and_general(hass):
    entry = _entry(hass, [dict(MB), {**MB, "id": "def", "username": "b@x.fr"}])
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"next_step_id": "mailbox_remove"})
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"id": ["abc"]})
    assert [m["id"] for m in entry.options[CONF_MAILBOXES]] == ["def"]

    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"next_step_id": "general"})
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"archive_after_days": 5, "email_interval": 30}
    )
    assert entry.options["archive_after_days"] == 5
    assert entry.options["email_interval"] == 30
    assert [m["id"] for m in entry.options[CONF_MAILBOXES]] == ["def"]


async def test_options_menu_without_mailboxes(hass):
    entry = _entry(hass, [])
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["menu_options"] == ["general", "mailbox_provider", "webhook"]


@pytest.mark.parametrize("ssl_flag,expected", [(True, "ssl"), (False, "starttls")])
async def test_migration_v1(hass, ssl_flag, expected):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "api_key": "k",
            "imap_server": "imap.free.fr",
            "imap_port": 993,
            "imap_user": "me@free.fr",
            "imap_password": "pw",
            "imap_folder": "Colis",
            "imap_ssl": ssl_flag,
            "imap_dedicated": True,
        },
        options={"archive_after_days": 3, "email_interval": 20},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert set(entry.data) == {"api_key", "webhook_id"}
    assert entry.options["archive_after_days"] == 3
    (mb,) = entry.options[CONF_MAILBOXES]
    assert mb["server"] == "imap.free.fr" and mb["security"] == expected
    assert mb["username"] == "me@free.fr" and mb["password"] == "pw"
    assert mb["folders"] == ["Colis"] and mb["dedicated"] is True and mb["mark_seen"] is True


async def test_migration_v1_without_imap(hass):
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={"api_key": "k"}, options={})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_MAILBOXES] == []


async def test_coordinator_checks_all_enabled_mailboxes(hass):
    store = MagicMock()
    store.packages = {"EXISTING1234": object()}
    options = {
        CONF_MAILBOXES: [
            {**MB, "id": "1", "name": "A"},
            {**MB, "id": "2", "name": "B"},
            {**MB, "id": "3", "name": "Off", "enabled": False},
        ]
    }
    from homeassistant.config_entries import current_entry

    current_entry.set(_entry(hass, options[CONF_MAILBOXES]))
    # pre-existing: the coordinator does not pass config_entry (HA deprecation report)
    with patch("homeassistant.helpers.frame.report_usage"):
        coord = SuiviColisCoordinator(hass, MagicMock(), store, {"api_key": "k"}, options)
    assert coord.imap_configured
    seen_known = []

    def fake_fetch(mailbox, known):
        seen_known.append(set(known))
        if mailbox["id"] == "2":
            raise OSError("boom")
        known.add("1Z9999999999999999")
        return [ExtractedPackage("1Z9999999999999999", "ups", "Colis", "ups")]

    coord.add_package = AsyncMock()
    with patch("custom_components.suivi_colis.coordinator.run_imap_fetch", side_effect=fake_fetch):
        await coord._check_emails()

    assert len(seen_known) == 2  # disabled mailbox skipped
    assert "1Z9999999999999999" in seen_known[1]  # dedup shared across mailboxes
    coord.add_package.assert_awaited_once()
    assert coord.mailbox_status["1"]["found"] == 1 and coord.mailbox_status["1"]["error"] is None
    assert coord.mailbox_status["2"]["error"] == "boom"
    assert "3" not in coord.mailbox_status
