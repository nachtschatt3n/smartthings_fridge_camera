"""The Samsung FamilyHub Fridge integration.

Auth modes (data["auth_mode"]):

- "samsung_account" : Samsung Account email + password. The integration
             re-logs in on every 401 to produce a fresh OEM bearer token
             that works on the Samsung-proprietary view-inside camera
             endpoint. This is the only mode that unlocks the camera feed.

- "pat"    : Legacy SmartThings Personal Access Token (raw string). Samsung
             deprecated indefinite PATs on 2024-12-30 — new PATs expire after
             24 hours. Retained for backwards compatibility. Does NOT enable
             the camera feed.

Config entries created before this integration version stored `{token, device_id}`
without an `auth_mode` key; they are migrated to `auth_mode: "pat"` on first
load via `async_migrate_entry`.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

from .api import FamilyHub
from .const import (
    AUTH_MODE_PAT,
    AUTH_MODE_SAMSUNG,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_SAMSUNG_ACCESS_TOKEN,
    CONF_SAMSUNG_EMAIL,
    CONF_SAMSUNG_PASSWORD,
    CONF_SIGNIN_CLIENT_ID,
    CONF_SIGNIN_CLIENT_SECRET,
    CONF_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Samsung FamilyHub Fridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    auth_mode = entry.data.get(CONF_AUTH_MODE, AUTH_MODE_PAT)
    device_id = entry.data.get(CONF_DEVICE_ID)

    if auth_mode == AUTH_MODE_SAMSUNG:
        hub = await _build_samsung_hub(hass, entry, device_id)
    else:
        # Legacy PAT path — unchanged from v0.0.x.
        token = entry.data.get(CONF_TOKEN)
        if not token:
            raise ConfigEntryNotReady(
                "PAT-mode config entry has no token. Reconfigure the "
                "integration in Settings → Devices & Services."
            )
        hub = FamilyHub(hass, token=token, device_id=device_id)

    hass.data[DOMAIN][entry.entry_id] = entry
    hass.data[DOMAIN]["hub"] = hub

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_refresh(call: ServiceCall) -> None:
        """Manually trigger a fridge camera refresh."""
        _LOGGER.info("Manual refresh requested — sending update_camera command")
        await hub.async_ensure_fresh_token()
        await hass.async_add_executor_job(hub.update_camera)
        hub.should_update = False  # already sent the command
        _LOGGER.info("Manual refresh command sent successfully")

    hass.services.async_register(DOMAIN, "refresh", _handle_refresh)

    return True


async def _build_samsung_hub(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str | None
) -> FamilyHub:
    """Construct a FamilyHub authenticated via Samsung Account email/password.

    Uses the access_token stored in the config entry if present. On 401,
    `FamilyHub.async_relogin_samsung()` re-submits credentials to get a
    fresh token — this callback persists it back to the config entry so
    HA restarts resume with a working token.
    """
    email = entry.data.get(CONF_SAMSUNG_EMAIL)
    password = entry.data.get(CONF_SAMSUNG_PASSWORD)
    signin_client_id = entry.data.get(CONF_SIGNIN_CLIENT_ID)
    signin_client_secret = entry.data.get(CONF_SIGNIN_CLIENT_SECRET)
    if not all([email, password, signin_client_id, signin_client_secret]):
        raise ConfigEntryNotReady(
            "Samsung Account entry missing credentials. Reconfigure in Settings."
        )

    # Best-effort: use the stored token first; if None/stale, the first API
    # call will 401 and the coordinator will re-login automatically.
    token = entry.data.get(CONF_SAMSUNG_ACCESS_TOKEN) or ""
    hub = FamilyHub(hass, token=token, device_id=device_id)

    def _persist_token(new_token: str) -> None:
        new_data = {**entry.data, CONF_SAMSUNG_ACCESS_TOKEN: new_token}
        hass.config_entries.async_update_entry(entry, data=new_data)

    hub.attach_samsung_credentials(
        email=email,
        password=password,
        signin_client_id=signin_client_id,
        signin_client_secret=signin_client_secret,
        on_token_updated=_persist_token,
    )

    # If we have no token at all, do an initial login so the first poll
    # doesn't need a 401-retry round-trip.
    if not token:
        try:
            await hub.async_relogin_samsung()
        except Exception as err:  # pylint: disable=broad-except
            raise ConfigEntryNotReady(
                f"Initial Samsung Account login failed: {err}"
            ) from err

    return hub


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (e.g. after re-authentication)."""
    hub: FamilyHub = hass.data[DOMAIN]["hub"]
    auth_mode = entry.data.get(CONF_AUTH_MODE, AUTH_MODE_PAT)
    if auth_mode == AUTH_MODE_PAT:
        new_token = entry.data.get(CONF_TOKEN)
        if new_token:
            hub.update_token(new_token)
    # OAuth mode refreshes its token automatically — nothing to do here.


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop("hub", None)
        # Only deregister the service when no other entries remain
        if not [
            eid for eid in hass.data[DOMAIN] if eid != "hub"
        ]:
            hass.services.async_remove(DOMAIN, "refresh")

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry data to the current schema.

    v1  → {token, device_id}                     (raw PAT)
    v2  → adds auth_mode=(pat|oauth), optional linked_smartthings_entry_id
    """
    if entry.version == 1:
        new_data: dict[str, Any] = {**entry.data, CONF_AUTH_MODE: AUTH_MODE_PAT}
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info(
            "Migrated samsung_familyhub_fridge entry %s v1→v2 (auth_mode=pat). "
            "Reconfigure in UI to switch to OAuth.",
            entry.entry_id,
        )
    return True
