"""Config flow for Samsung FamilyHub Fridge integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import AuthenticationError, FamilyHub
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
    DEFAULT_SIGNIN_CLIENT_ID,
    DEFAULT_SIGNIN_CLIENT_SECRET,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


STEP_PAT_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_DEVICE_ID): str,
    }
)


async def _validate_pat(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate a Personal Access Token and resolve the device ID."""
    hub = FamilyHub(hass, data[CONF_TOKEN], data.get(CONF_DEVICE_ID))

    try:
        if not await hub.authenticate():
            raise InvalidAuth
    except AuthenticationError as err:
        raise InvalidAuth from err

    if not data.get(CONF_DEVICE_ID):
        data[CONF_DEVICE_ID] = hub.device_id

    return data


async def _validate_samsung_account(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Log into Samsung Account with email/password, validate, return creds + token."""
    # Lazy import to keep config_flow light for tests
    from .auth import SamsungAccountAuth

    # Use APK-extracted defaults if the user didn't override (typical case).
    signin_id = data.get(CONF_SIGNIN_CLIENT_ID) or DEFAULT_SIGNIN_CLIENT_ID
    signin_sec = data.get(CONF_SIGNIN_CLIENT_SECRET) or DEFAULT_SIGNIN_CLIENT_SECRET

    auth = SamsungAccountAuth(
        email=data[CONF_SAMSUNG_EMAIL],
        password=data[CONF_SAMSUNG_PASSWORD],
        signin_client_id=signin_id,
        signin_client_secret=signin_sec,
    )
    try:
        creds = await hass.async_add_executor_job(auth.login)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.warning("Samsung Account login failed: %s", err)
        raise InvalidAuth from err

    # Probe the fridge with the resulting bearer token
    hub = FamilyHub(hass, token=creds.access_token, device_id=data.get(CONF_DEVICE_ID))
    try:
        if not await hub.authenticate():
            raise InvalidAuth
    except AuthenticationError as err:
        raise InvalidAuth from err

    return {
        CONF_AUTH_MODE: AUTH_MODE_SAMSUNG,
        CONF_SAMSUNG_EMAIL: data[CONF_SAMSUNG_EMAIL],
        CONF_SAMSUNG_PASSWORD: data[CONF_SAMSUNG_PASSWORD],
        CONF_SIGNIN_CLIENT_ID: signin_id,
        CONF_SIGNIN_CLIENT_SECRET: signin_sec,
        CONF_SAMSUNG_ACCESS_TOKEN: creds.access_token,
        CONF_DEVICE_ID: data.get(CONF_DEVICE_ID) or hub.device_id,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Samsung FamilyHub Fridge."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step — menu: pick auth mode.

        Two options: Samsung Account (camera + everything) or PAT (legacy,
        24h expiry, no camera). The 'oauth' (reuse HA core smartthings)
        mode was removed because it cannot reach the Samsung-proprietary
        view-inside camera endpoint — if you only need generic fridge
        data, HA core's SmartThings integration already provides it.
        """
        return self.async_show_menu(
            step_id="user",
            menu_options=["samsung_account", "pat"],
        )

    # ---------------- OAuth path ----------------

    # --- Helpers -------------------------------------------------------------

    def _is_existing_entry_flow(self) -> bool:
        """True if this flow is reauth- or reconfigure-ing an existing entry."""
        return self.source in (
            config_entries.SOURCE_REAUTH,
            config_entries.SOURCE_RECONFIGURE,
        )

    # ---------------- Samsung Account path (camera feed) ----------------

    async def async_step_samsung_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Samsung Account email/password login — supports camera feed."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await _validate_samsung_account(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during Samsung Account validation")
                errors["base"] = "unknown"
            else:
                if self._is_existing_entry_flow():
                    existing = (
                        self._get_reauth_entry()
                        if self.source == config_entries.SOURCE_REAUTH
                        else self._get_reconfigure_entry()
                    )
                    return self.async_update_reload_and_abort(existing, data=data)
                return self.async_create_entry(
                    title="Samsung Fridge Camera (Samsung Account)", data=data
                )

        # Only email+password are required; signin_client_id/secret default to
        # APK-extracted constants (6iado3s6jc / USING_CLIENT_PACKAGE_INFORMATION)
        # and only need to be overridden if a future SmartThings version ships
        # with different values.
        schema = vol.Schema(
            {
                vol.Required(CONF_SAMSUNG_EMAIL): str,
                vol.Required(CONF_SAMSUNG_PASSWORD): str,
                vol.Optional(CONF_SIGNIN_CLIENT_ID, default=DEFAULT_SIGNIN_CLIENT_ID): str,
                vol.Optional(CONF_SIGNIN_CLIENT_SECRET, default=DEFAULT_SIGNIN_CLIENT_SECRET): str,
                vol.Optional(CONF_DEVICE_ID): str,
            }
        )
        return self.async_show_form(
            step_id="samsung_account", data_schema=schema, errors=errors
        )

    # ---------------- PAT path (legacy) ----------------

    async def async_step_pat(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enter a raw SmartThings Personal Access Token (legacy, 24h expiry)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await _validate_pat(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                data = {**info, CONF_AUTH_MODE: AUTH_MODE_PAT}
                if self._is_existing_entry_flow():
                    existing = (
                        self._get_reauth_entry()
                        if self.source == config_entries.SOURCE_REAUTH
                        else self._get_reconfigure_entry()
                    )
                    return self.async_update_reload_and_abort(existing, data=data)
                return self.async_create_entry(
                    title="Samsung Fridge Camera", data=data
                )

        return self.async_show_form(
            step_id="pat", data_schema=STEP_PAT_DATA_SCHEMA, errors=errors
        )

    # ---------------- Reauth / Reconfigure ----------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle re-authentication when the token has expired.

        Always route to the menu so users can switch between Samsung
        Account and PAT modes when their PAT expires (24h) rather than
        being stuck re-entering new PATs forever.
        """
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Reconfigure button on the integration card.

        Routes to the same menu the initial setup uses — lets users
        switch between Samsung Account and PAT modes.
        """
        return await self.async_step_user()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle PAT re-authentication."""
        errors: dict[str, str] = {}
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            new_data = {
                **reauth_entry.data,
                CONF_TOKEN: user_input[CONF_TOKEN],
                CONF_AUTH_MODE: AUTH_MODE_PAT,
            }
            try:
                await _validate_pat(self.hass, new_data)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during re-auth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=new_data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
