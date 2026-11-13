"""Suivi de Colis — Package tracking integration for Home Assistant."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import aiohttp
from homeassistant.components.webhook import async_generate_id as webhook_generate_id
import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api_17track import Api17TrackClient, Api17TrackError, to_17track_lang
from .const import (
    CHANGE_CARRIER_OPTIONS,
    CONF_API_KEY,
    CONF_IMAP_DEDICATED,
    CONF_IMAP_FOLDER,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_IMAP_SSL,
    CONF_IMAP_USER,
    CONF_MAILBOXES,
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    DEFAULT_IMAP_FOLDER,
    DEFAULT_MAX_EMAILS,
    DEFAULT_SEARCH_DAYS,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MB_DEDICATED,
    MB_ENABLED,
    MB_FOLDERS,
    MB_ID,
    MB_MARK_SEEN,
    MB_MAX_EMAILS,
    MB_NAME,
    MB_PASSWORD,
    MB_PORT,
    MB_PROVIDER,
    MB_SEARCH_DAYS,
    MB_SECURITY,
    MB_SERVER,
    MB_TIMEOUT,
    MB_UNSEEN_ONLY,
    MB_USERNAME,
    MB_VERIFY_SSL,
    PROVIDER_CUSTOM,
    SECURITY_SSL,
    SECURITY_STARTTLS,
)
from .coordinator import SuiviColisCoordinator
from .store import SuiviColisStore
from .webhook import async_register_webhook, async_unregister_webhook

_LOGGER = logging.getLogger(__name__)

CARD_JS_URL = "/suivi_colis/suivi-colis-card.js"
CARD_JS_VERSION = "1.2.0"

PLATFORMS = ["sensor", "binary_sensor", "event"]

SERVICE_ADD_PACKAGE = "add_package"
SERVICE_REMOVE_PACKAGE = "remove_package"
SERVICE_REFRESH = "refresh"
SERVICE_ARCHIVE_DELIVERED = "archive_delivered"
SERVICE_CHANGE_CARRIER = "change_carrier"

ADD_PACKAGE_SCHEMA = vol.Schema(
    {
        vol.Required("tracking_number"): cv.string,
        vol.Optional("friendly_name", default=""): cv.string,
        vol.Optional("carrier", default=""): cv.string,
    }
)

REMOVE_PACKAGE_SCHEMA = vol.Schema(
    {
        vol.Required("tracking_number"): cv.string,
    }
)

CHANGE_CARRIER_SCHEMA = vol.Schema(
    {
        vol.Required("tracking_number"): cv.string,
        vol.Optional("carrier"): vol.In(list(CHANGE_CARRIER_OPTIONS)),
        # Any 17track carrier code (apicarrier.all.json), overrides "carrier"
        vol.Optional("carrier_code"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Suivi de Colis — register static files and Lovelace resource."""
    # Serve www/ folder at /suivi_colis/
    www_path = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_JS_URL, str(www_path / "suivi-colis-card.js"), cache_headers=False)]
    )

    # Register Lovelace resource after HA is fully started (resources not ready earlier)
    url = f"{CARD_JS_URL}?v={CARD_JS_VERSION}"

    async def _register_on_start(event: Event) -> None:
        await _async_register_lovelace_resource(hass, url)

    if hass.is_running:
        await _async_register_lovelace_resource(hass, url)
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_on_start)

    return True


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Register the card JS as a Lovelace resource if not already present."""
    try:
        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            _LOGGER.warning("Lovelace not available, add resource manually: %s", url)
            return

        # lovelace_data can be a dict or object with resources attribute
        resources = None
        if isinstance(lovelace_data, dict):
            resources = lovelace_data.get("resources")
        elif hasattr(lovelace_data, "resources"):
            resources = lovelace_data.resources

        if resources is None:
            _LOGGER.warning("Lovelace resources not available (YAML mode?), add manually: %s", url)
            return

        # Clean up old lemon_tracker resources and check if already registered
        found = False
        for item in resources.async_items():
            item_url = item.get("url", "")
            # Remove legacy lemon_tracker resource
            if "/lemon_tracker/" in item_url or "lemon-tracker-card" in item_url:
                await resources.async_delete_item(item["id"])
                _LOGGER.info("Removed legacy lemon_tracker resource: %s", item_url)
                continue
            # Check if current resource already exists
            if item_url.startswith(CARD_JS_URL):
                if item_url != url:
                    await resources.async_update_item(
                        item["id"], {"url": url}
                    )
                    _LOGGER.info("Updated Suivi de Colis card resource to %s", url)
                found = True

        if not found:
            await resources.async_create_item({"res_type": "module", "url": url})
            _LOGGER.info("Registered Suivi de Colis card resource: %s", url)
    except Exception as err:
        _LOGGER.error("Could not auto-register Lovelace resource: %s — add manually: %s", err, url)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version > 2:
        return False  # Downgrade not supported

    if entry.version == 1:
        # v1: a single IMAP mailbox stored in entry.data
        # v2: a list of mailboxes stored in entry.options[CONF_MAILBOXES]
        data = dict(entry.data)
        options = dict(entry.options)
        mailboxes = list(options.get(CONF_MAILBOXES, []))

        if data.get(CONF_IMAP_SERVER):
            dedicated = data.get(CONF_IMAP_DEDICATED, False)
            mailboxes.append(
                {
                    MB_ID: uuid.uuid4().hex,
                    MB_PROVIDER: PROVIDER_CUSTOM,
                    MB_NAME: data.get(CONF_IMAP_USER) or data[CONF_IMAP_SERVER],
                    MB_SERVER: data[CONF_IMAP_SERVER],
                    MB_PORT: data.get(CONF_IMAP_PORT, 993),
                    # "SSL" unchecked was meant as STARTTLS in v1
                    MB_SECURITY: SECURITY_SSL
                    if data.get(CONF_IMAP_SSL, True)
                    else SECURITY_STARTTLS,
                    MB_VERIFY_SSL: True,
                    MB_TIMEOUT: DEFAULT_TIMEOUT,
                    MB_USERNAME: data.get(CONF_IMAP_USER, ""),
                    MB_PASSWORD: data.get(CONF_IMAP_PASSWORD, ""),
                    MB_FOLDERS: [data.get(CONF_IMAP_FOLDER) or DEFAULT_IMAP_FOLDER],
                    MB_DEDICATED: dedicated,
                    MB_UNSEEN_ONLY: False,
                    MB_MARK_SEEN: dedicated,
                    MB_SEARCH_DAYS: DEFAULT_SEARCH_DAYS,
                    MB_MAX_EMAILS: DEFAULT_MAX_EMAILS,
                    MB_ENABLED: True,
                }
            )

        new_data = {k: v for k, v in data.items() if not k.startswith("imap_")}
        options[CONF_MAILBOXES] = mailboxes
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=options, version=2, minor_version=1
        )
        _LOGGER.info("Migrated Suivi de Colis config to v2 (%d mailbox(es))", len(mailboxes))

    if entry.version == 2 and entry.minor_version < 2:
        # 2.2: secret webhook id for 17track push notifications (disabled by
        # default). Minor bump: an older 2.x version still loads the entry.
        data = dict(entry.data)
        data.setdefault(CONF_WEBHOOK_ID, webhook_generate_id())
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Suivi de Colis from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create HTTP session and API client
    session = aiohttp.ClientSession()
    api_client = Api17TrackClient(
        session, entry.data[CONF_API_KEY], lang=to_17track_lang(hass.config.language)
    )

    # Load stored packages
    store = SuiviColisStore(hass)
    await store.async_load()

    # Create coordinator
    coordinator = SuiviColisCoordinator(
        hass,
        api_client=api_client,
        store=store,
        config=dict(entry.data),
        options=dict(entry.options),
        config_entry=entry,
    )

    # Listen for option updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Store references
    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN]["session"] = session

    # Initial refresh
    await coordinator.async_config_entry_first_refresh()

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 17track push notifications
    _async_sync_webhook(hass, entry, coordinator)

    # Register services
    _register_services(hass, coordinator)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        if entry.data.get(CONF_WEBHOOK_ID):
            async_unregister_webhook(hass, entry.data[CONF_WEBHOOK_ID])
        hass.data[DOMAIN].pop(entry.entry_id, None)
        session = hass.data[DOMAIN].pop("session", None)
        if session:
            await session.close()

        # Remove services if no entries left
        remaining = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining:
            for service in [
                SERVICE_ADD_PACKAGE,
                SERVICE_REMOVE_PACKAGE,
                SERVICE_REFRESH,
                SERVICE_ARCHIVE_DELIVERED,
                SERVICE_CHANGE_CARRIER,
            ]:
                hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    coordinator: SuiviColisCoordinator = hass.data[DOMAIN][entry.entry_id]
    old_mailboxes = {mb[MB_ID]: mb.get(MB_NAME) for mb in coordinator.options.get(CONF_MAILBOXES, [])}
    coordinator.options = dict(entry.options)
    new_mailboxes = {mb[MB_ID]: mb.get(MB_NAME) for mb in entry.options.get(CONF_MAILBOXES, [])}
    if new_mailboxes != old_mailboxes:
        # Mailbox added, removed or renamed: reload to create/remove their
        # diagnostic entities
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    _async_sync_webhook(hass, entry, coordinator)


def _async_sync_webhook(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SuiviColisCoordinator
) -> None:
    """Register or unregister the 17track webhook according to the options."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return
    async_unregister_webhook(hass, webhook_id)
    if entry.options.get(CONF_WEBHOOK_ENABLED, False):
        async_register_webhook(hass, webhook_id, entry.data[CONF_API_KEY], coordinator)


def _register_services(
    hass: HomeAssistant, coordinator: SuiviColisCoordinator
) -> None:
    """Register integration services."""

    async def handle_add_package(call: ServiceCall) -> None:
        tracking_number = call.data["tracking_number"]
        friendly_name = call.data.get("friendly_name", "")
        carrier = call.data.get("carrier", "")
        result = await coordinator.add_package(
            tracking_number=tracking_number,
            carrier=carrier,
            friendly_name=friendly_name,
        )
        if result:
            _LOGGER.info("Added package %s", tracking_number)
        else:
            _LOGGER.warning("Failed to add package %s", tracking_number)

    async def handle_remove_package(call: ServiceCall) -> None:
        tracking_number = call.data["tracking_number"]
        result = await coordinator.remove_package(tracking_number)
        if result:
            _LOGGER.info("Removed package %s", tracking_number)
        else:
            _LOGGER.warning("Package %s not found", tracking_number)

    async def handle_refresh(call: ServiceCall) -> None:
        await coordinator.async_request_refresh()

    async def handle_change_carrier(call: ServiceCall) -> None:
        number = call.data["tracking_number"].strip().upper()
        if "carrier_code" in call.data:
            code = call.data["carrier_code"]
            name = call.data.get("carrier") or ""
            if name in CHANGE_CARRIER_OPTIONS:
                name = CHANGE_CARRIER_OPTIONS[name][1]
        elif "carrier" in call.data:
            code, name = CHANGE_CARRIER_OPTIONS[call.data["carrier"]]
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="carrier_required"
            )
        if coordinator.store.get_package(number) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="package_not_found",
                translation_placeholders={"tracking_number": number},
            )
        try:
            await coordinator.change_carrier(number, code, name)
        except Api17TrackError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="change_carrier_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def handle_archive_delivered(call: ServiceCall) -> None:
        count = await coordinator.archive_delivered()
        _LOGGER.info("Archived %d delivered packages", count)

    if not hass.services.has_service(DOMAIN, SERVICE_ADD_PACKAGE):
        hass.services.async_register(
            DOMAIN, SERVICE_ADD_PACKAGE, handle_add_package, schema=ADD_PACKAGE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_REMOVE_PACKAGE, handle_remove_package, schema=REMOVE_PACKAGE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_REFRESH, handle_refresh
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ARCHIVE_DELIVERED, handle_archive_delivered
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CHANGE_CARRIER,
            handle_change_carrier,
            schema=CHANGE_CARRIER_SCHEMA,
        )
