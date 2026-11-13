"""Config flow for Suivi de Colis."""

from __future__ import annotations

import copy
import logging
import uuid
from functools import partial
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.webhook import async_generate_id as webhook_generate_id
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api_17track import Api17TrackClient, Api17TrackError
from .const import (
    CONF_API_KEY,
    CONF_ARCHIVE_AFTER_DAYS,
    CONF_EMAIL_INTERVAL,
    CONF_MAILBOXES,
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_EMAIL_INTERVAL,
    DEFAULT_IMAP_FOLDER,
    DEFAULT_MAX_EMAILS,
    DEFAULT_PORTS,
    DEFAULT_SEARCH_DAYS,
    DEFAULT_SECURITY,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    IMAP_PROVIDERS,
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
    SECURITY_MODES,
)

_LOGGER = logging.getLogger(__name__)

STEP_ADD_MAILBOX = "mailbox_provider"


def _number(minimum: int, maximum: int, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _mailbox_label(mailbox: dict[str, Any]) -> str:
    name = mailbox.get(MB_NAME) or mailbox.get(MB_USERNAME, "")
    return f"{name} ({mailbox.get(MB_USERNAME, '')} @ {mailbox.get(MB_SERVER, '')})"


def _mailboxes_summary(mailboxes: list[dict[str, Any]]) -> str:
    if not mailboxes:
        return "—"
    lines = []
    for mb in mailboxes:
        state = "✅" if mb.get(MB_ENABLED, True) else "⏸️"
        folders = ", ".join(mb.get(MB_FOLDERS) or [DEFAULT_IMAP_FOLDER])
        lines.append(f"{state} {_mailbox_label(mb)} — {folders}")
    return "\n".join(lines)


class MailboxFlowMixin:
    """Add/edit mailbox steps, shared by the config flow and the options flow.

    Flow: provider preset -> connection (tested live) -> scan settings.
    """

    _mailboxes: list[dict[str, Any]]
    _mb_editing: dict[str, Any] | None = None  # mailbox being edited, None = new
    _mb_draft: dict[str, Any]
    _mb_server_folders: list[str]

    async def _async_mailbox_saved(
        self, mailbox: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Called once a mailbox is fully configured."""
        raise NotImplementedError

    def _store_mailbox(self, mailbox: dict[str, Any]) -> None:
        for idx, existing in enumerate(self._mailboxes):
            if existing[MB_ID] == mailbox[MB_ID]:
                self._mailboxes[idx] = mailbox
                return
        self._mailboxes.append(mailbox)

    async def async_step_mailbox_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick a provider to prefill the connection settings."""
        if user_input is not None:
            provider = user_input[MB_PROVIDER]
            self._mb_editing = None
            self._mb_draft = {MB_PROVIDER: provider}
            if provider in IMAP_PROVIDERS:
                server, port, security = IMAP_PROVIDERS[provider]
                self._mb_draft.update(
                    {MB_SERVER: server, MB_PORT: port, MB_SECURITY: security}
                )
            return await self.async_step_mailbox_connection()

        return self.async_show_form(
            step_id="mailbox_provider",
            data_schema=vol.Schema(
                {
                    vol.Required(MB_PROVIDER, default=PROVIDER_CUSTOM): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[PROVIDER_CUSTOM, *IMAP_PROVIDERS],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="provider",
                        )
                    ),
                }
            ),
        )

    async def async_step_mailbox_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Server, security and credentials. The connection is tested on submit."""
        errors: dict[str, str] = {}
        placeholders = {"error_detail": ""}
        draft = self._mb_draft
        editing = self._mb_editing is not None

        if user_input is not None:
            server = user_input[MB_SERVER].strip()
            security = user_input.get(MB_SECURITY, DEFAULT_SECURITY)
            port = int(user_input.get(MB_PORT) or DEFAULT_PORTS[security])
            username = user_input[MB_USERNAME].strip()
            password = user_input.get(MB_PASSWORD) or (
                self._mb_editing.get(MB_PASSWORD, "") if editing else ""
            )
            verify_ssl = user_input.get(MB_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            timeout = int(user_input.get(MB_TIMEOUT) or DEFAULT_TIMEOUT)

            draft.update(
                {
                    MB_NAME: (user_input.get(MB_NAME) or "").strip(),
                    MB_SERVER: server,
                    MB_PORT: port,
                    MB_SECURITY: security,
                    MB_VERIFY_SSL: verify_ssl,
                    MB_TIMEOUT: timeout,
                    MB_USERNAME: username,
                }
            )

            current_id = self._mb_editing[MB_ID] if editing else None
            if not password:
                errors[MB_PASSWORD] = "password_required"
            elif any(
                mb[MB_ID] != current_id
                and mb[MB_SERVER].lower() == server.lower()
                and mb[MB_USERNAME].lower() == username.lower()
                for mb in self._mailboxes
            ):
                errors["base"] = "mailbox_exists"
            else:
                from .email_parser import check_imap_connection

                result = await self.hass.async_add_executor_job(
                    partial(
                        check_imap_connection,
                        server,
                        port,
                        username,
                        password,
                        security,
                        verify_ssl,
                        timeout,
                    )
                )
                if result.error:
                    errors["base"] = result.error
                    placeholders["error_detail"] = result.detail
                else:
                    draft[MB_PASSWORD] = password
                    self._mb_server_folders = result.folders
                    return await self.async_step_mailbox_settings()

        password_key = (
            vol.Optional(MB_PASSWORD) if editing else vol.Required(MB_PASSWORD)
        )
        port = draft.get(MB_PORT)
        return self.async_show_form(
            step_id="mailbox_connection",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        MB_NAME, description={"suggested_value": draft.get(MB_NAME)}
                    ): selector.TextSelector(),
                    vol.Required(
                        MB_SERVER, default=draft.get(MB_SERVER, "")
                    ): selector.TextSelector(),
                    vol.Required(
                        MB_SECURITY, default=draft.get(MB_SECURITY, DEFAULT_SECURITY)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=SECURITY_MODES,
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="security",
                        )
                    ),
                    vol.Optional(
                        MB_PORT, description={"suggested_value": port}
                    ): _number(1, 65535),
                    vol.Required(
                        MB_VERIFY_SSL,
                        default=draft.get(MB_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        MB_USERNAME, default=draft.get(MB_USERNAME, "")
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(autocomplete="username")
                    ),
                    password_key: selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                    vol.Required(
                        MB_TIMEOUT, default=draft.get(MB_TIMEOUT, DEFAULT_TIMEOUT)
                    ): _number(5, 300, "s"),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_mailbox_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Folders and scan behaviour."""
        errors: dict[str, str] = {}
        draft = self._mb_draft

        if user_input is not None:
            folders = [f.strip() for f in user_input.get(MB_FOLDERS, []) if f.strip()]
            if not folders:
                errors[MB_FOLDERS] = "no_folder"
            else:
                mailbox = {
                    MB_ID: draft.get(MB_ID) or uuid.uuid4().hex,
                    MB_PROVIDER: draft.get(MB_PROVIDER, PROVIDER_CUSTOM),
                    MB_NAME: draft.get(MB_NAME) or draft[MB_USERNAME],
                    MB_SERVER: draft[MB_SERVER],
                    MB_PORT: draft[MB_PORT],
                    MB_SECURITY: draft[MB_SECURITY],
                    MB_VERIFY_SSL: draft[MB_VERIFY_SSL],
                    MB_TIMEOUT: draft[MB_TIMEOUT],
                    MB_USERNAME: draft[MB_USERNAME],
                    MB_PASSWORD: draft[MB_PASSWORD],
                    MB_FOLDERS: folders,
                    MB_DEDICATED: user_input[MB_DEDICATED],
                    MB_UNSEEN_ONLY: user_input[MB_UNSEEN_ONLY],
                    MB_MARK_SEEN: user_input[MB_MARK_SEEN],
                    MB_SEARCH_DAYS: int(user_input[MB_SEARCH_DAYS]),
                    MB_MAX_EMAILS: int(user_input[MB_MAX_EMAILS]),
                    MB_ENABLED: user_input[MB_ENABLED],
                }
                self._mb_editing = None
                return await self._async_mailbox_saved(mailbox)

        selected = draft.get(MB_FOLDERS) or [DEFAULT_IMAP_FOLDER]
        options = list(self._mb_server_folders)
        options += [f for f in selected if f not in options]

        return self.async_show_form(
            step_id="mailbox_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(MB_FOLDERS, default=selected): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        MB_DEDICATED, default=draft.get(MB_DEDICATED, False)
                    ): selector.BooleanSelector(),
                    vol.Required(
                        MB_SEARCH_DAYS,
                        default=draft.get(MB_SEARCH_DAYS, DEFAULT_SEARCH_DAYS),
                    ): _number(0, 90, "j"),
                    vol.Required(
                        MB_MAX_EMAILS,
                        default=draft.get(MB_MAX_EMAILS, DEFAULT_MAX_EMAILS),
                    ): _number(0, 1000),
                    vol.Required(
                        MB_UNSEEN_ONLY, default=draft.get(MB_UNSEEN_ONLY, False)
                    ): selector.BooleanSelector(),
                    vol.Required(
                        MB_MARK_SEEN,
                        default=draft.get(MB_MARK_SEEN, draft.get(MB_DEDICATED, False)),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        MB_ENABLED, default=draft.get(MB_ENABLED, True)
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "server": draft.get(MB_SERVER, ""),
                "folder_count": str(len(self._mb_server_folders)),
            },
        )


class SuiviColisConfigFlow(MailboxFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Suivi de Colis."""

    VERSION = 2
    MINOR_VERSION = 2  # 2.2: webhook id (see async_migrate_entry)

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}
        self._mailboxes = []
        self._mb_draft = {}
        self._mb_server_folders = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: 17track API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()

            try:
                async with aiohttp.ClientSession() as session:
                    client = Api17TrackClient(session, api_key)
                    await client.validate_api_key()
            except Api17TrackError:
                errors["base"] = "invalid_api_key"
            except Exception:
                errors["base"] = "cannot_connect"

            if not errors:
                self._data[CONF_API_KEY] = api_key
                return await self.async_step_mailboxes()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"api_url": "https://api.17track.net"},
        )

    async def async_step_mailboxes(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: add as many mailboxes as wanted (optional)."""
        return self.async_show_menu(
            step_id="mailboxes",
            menu_options=[STEP_ADD_MAILBOX, "finish"],
            description_placeholders={"mailboxes": _mailboxes_summary(self._mailboxes)},
        )

    async def _async_mailbox_saved(
        self, mailbox: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._store_mailbox(mailbox)
        return await self.async_step_mailboxes()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(
            title="Suivi de Colis",
            data={**self._data, CONF_WEBHOOK_ID: webhook_generate_id()},
            options={
                CONF_ARCHIVE_AFTER_DAYS: DEFAULT_ARCHIVE_AFTER_DAYS,
                CONF_EMAIL_INTERVAL: DEFAULT_EMAIL_INTERVAL,
                CONF_MAILBOXES: self._mailboxes,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SuiviColisOptionsFlow:
        """Get options flow."""
        return SuiviColisOptionsFlow()


class SuiviColisOptionsFlow(MailboxFlowMixin, config_entries.OptionsFlow):
    """Options: general settings and mailbox management."""

    def __init__(self) -> None:
        """Initialize."""
        self._mailboxes: list[dict[str, Any]] | None = None  # type: ignore[assignment]
        self._mb_draft = {}
        self._mb_server_folders = []

    def _ensure_loaded(self) -> None:
        if self._mailboxes is None:
            self._mailboxes = copy.deepcopy(
                self.config_entry.options.get(CONF_MAILBOXES, [])
            )

    def _save(self, **changes: Any) -> config_entries.ConfigFlowResult:
        options = dict(self.config_entry.options)
        options[CONF_MAILBOXES] = self._mailboxes
        options.update(changes)
        return self.async_create_entry(title="", data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Main menu."""
        self._ensure_loaded()
        menu = ["general", STEP_ADD_MAILBOX]
        if self._mailboxes:
            menu += ["mailbox_edit", "mailbox_remove"]
        menu.append("webhook")
        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={"mailboxes": _mailboxes_summary(self._mailboxes)},
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """General settings."""
        if user_input is not None:
            return self._save(
                **{
                    CONF_ARCHIVE_AFTER_DAYS: int(user_input[CONF_ARCHIVE_AFTER_DAYS]),
                    CONF_EMAIL_INTERVAL: int(user_input[CONF_EMAIL_INTERVAL]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ARCHIVE_AFTER_DAYS,
                        default=options.get(
                            CONF_ARCHIVE_AFTER_DAYS, DEFAULT_ARCHIVE_AFTER_DAYS
                        ),
                    ): _number(0, 30, "j"),
                    vol.Required(
                        CONF_EMAIL_INTERVAL,
                        default=options.get(CONF_EMAIL_INTERVAL, DEFAULT_EMAIL_INTERVAL),
                    ): _number(5, 120, "min"),
                }
            ),
        )

    async def async_step_webhook(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """17track push notifications: show the URL to paste in 17track."""
        if user_input is not None:
            return self._save(**{CONF_WEBHOOK_ENABLED: user_input[CONF_WEBHOOK_ENABLED]})

        from .webhook import async_get_webhook_url

        webhook_id = self.config_entry.data.get(CONF_WEBHOOK_ID)
        url = await async_get_webhook_url(self.hass, webhook_id) if webhook_id else None
        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WEBHOOK_ENABLED,
                        default=self.config_entry.options.get(CONF_WEBHOOK_ENABLED, False),
                    ): selector.BooleanSelector(),
                }
            ),
            description_placeholders={
                "webhook_url": url or "—",
                "dashboard_url": "https://api.17track.net/admin/settings",
            },
            errors={} if url else {"base": "no_external_url"},
        )

    def _mailbox_choices(self) -> list[selector.SelectOptionDict]:
        return [
            selector.SelectOptionDict(value=mb[MB_ID], label=_mailbox_label(mb))
            for mb in self._mailboxes
        ]

    async def async_step_mailbox_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose the mailbox to edit."""
        if user_input is not None:
            mailbox = next(
                mb for mb in self._mailboxes if mb[MB_ID] == user_input[MB_ID]
            )
            self._mb_editing = mailbox
            self._mb_draft = dict(mailbox)
            return await self.async_step_mailbox_connection()

        return self.async_show_form(
            step_id="mailbox_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(MB_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._mailbox_choices())
                    ),
                }
            ),
        )

    async def async_step_mailbox_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove one or more mailboxes."""
        if user_input is not None:
            to_remove = set(user_input.get(MB_ID, []))
            self._mailboxes = [
                mb for mb in self._mailboxes if mb[MB_ID] not in to_remove
            ]
            return self._save()

        return self.async_show_form(
            step_id="mailbox_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(MB_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._mailbox_choices(), multiple=True
                        )
                    ),
                }
            ),
        )

    async def _async_mailbox_saved(
        self, mailbox: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._store_mailbox(mailbox)
        return self._save()
