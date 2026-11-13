"""17track webhook (push) receiver.

17track POSTs one notification per tracking number to the URL configured in
its dashboard (Settings > WebHook). Payload, per https://api.17track.net/en/doc:

    {"event": "TRACKING_UPDATED" | "TRACKING_STOPPED", "data": {...}}

where ``data`` is one tracked item ({number, carrier, track_info, ...}); some
examples of the doc wrap it as {"accepted": [...]}, both shapes are accepted.

The request carries a ``sign`` header: SHA-256 hex digest of
``<raw body> + "/" + <17track security key>``. Unsigned or wrongly signed
requests are rejected, so knowing the webhook URL is not enough to inject data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SuiviColisCoordinator

_LOGGER = logging.getLogger(__name__)

WEBHOOK_NAME = "Suivi de Colis (17track)"
SUPPORTED_EVENTS = ("TRACKING_UPDATED", "TRACKING_STOPPED")


def compute_signature(body: str, api_key: str) -> str:
    """Signature 17track puts in the ``sign`` header."""
    return hashlib.sha256(f"{body}/{api_key}".encode()).hexdigest()


def extract_items(data: Any) -> list[dict]:
    """Tracked items of a notification, whatever the documented shape."""
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("accepted"), list):
            return [i for i in data["accepted"] if isinstance(i, dict)]
        if data.get("number"):
            return [data]
    return []


def async_register_webhook(
    hass: HomeAssistant, webhook_id: str, api_key: str, coordinator: SuiviColisCoordinator
) -> None:
    """Start receiving 17track notifications."""

    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        body = (await request.read()).decode("utf-8", errors="replace")
        sign = request.headers.get("sign", "")
        if not sign or not hmac.compare_digest(
            sign.lower(), compute_signature(body, api_key)
        ):
            _LOGGER.warning("Rejected 17track webhook call with an invalid signature")
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        try:
            payload = json.loads(body)
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)

        event = payload.get("event") if isinstance(payload, dict) else None
        if event not in SUPPORTED_EVENTS:
            # e.g. the dashboard's "WebHook test": acknowledge so 17track is happy
            _LOGGER.debug("17track webhook event %s acknowledged", event)
            return web.Response(status=HTTPStatus.OK)

        updated = await coordinator.async_handle_push(
            event, extract_items(payload.get("data"))
        )
        _LOGGER.debug("17track webhook %s: %d package(s) updated", event, updated)
        # Always 200 for a valid call: any other status makes 17track retry
        return web.Response(status=HTTPStatus.OK)

    webhook.async_register(
        hass, DOMAIN, WEBHOOK_NAME, webhook_id, _handle, allowed_methods=["POST"]
    )


def async_unregister_webhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Stop receiving 17track notifications."""
    webhook.async_unregister(hass, webhook_id)


async def async_get_webhook_url(hass: HomeAssistant, webhook_id: str) -> str | None:
    """Public URL to paste in 17track: Nabu Casa cloudhook, else external URL."""
    if "cloud" in hass.config.components:
        from homeassistant.components import cloud

        if cloud.async_active_subscription(hass):
            try:
                return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
            except Exception as err:  # cloud not connected yet, etc.
                _LOGGER.debug("Could not create Nabu Casa cloudhook: %s", err)
    try:
        return webhook.async_generate_url(hass, webhook_id, allow_internal=False)
    except NoURLAvailableError:
        return None
