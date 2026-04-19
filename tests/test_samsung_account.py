"""Unit tests for the Samsung Account auth mode.

Covers:
- FamilyHub.attach_samsung_credentials / async_relogin_samsung
- On 401, DataCoordinator triggers re-login and returns without surfacing
  ConfigEntryAuthFailed (recoverable path)
- Fresh-token callback fires so the caller can persist the new token
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    DataCoordinator,
    FamilyHub,
)


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass):
    return FamilyHub(hass, token="", device_id="device-123")


# ---------------------------------------------------------------------------
# FamilyHub.attach_samsung_credentials + relogin
# ---------------------------------------------------------------------------

async def test_attach_samsung_credentials_stores(hub):
    hub.attach_samsung_credentials(
        email="me@example.com",
        password="hunter2",
        signin_client_id="sci",
        signin_client_secret="scs",
    )
    assert hub._samsung_credentials == {
        "email": "me@example.com",
        "password": "hunter2",
        "signin_client_id": "sci",
        "signin_client_secret": "scs",
    }


async def test_async_relogin_samsung_updates_token(hub):
    callback = MagicMock()
    hub.attach_samsung_credentials(
        email="me@example.com",
        password="hunter2",
        signin_client_id="sci",
        signin_client_secret="scs",
        on_token_updated=callback,
    )

    fake_creds = MagicMock()
    fake_creds.access_token = "fresh-samsung-token"

    with patch(
        "custom_components.samsung_familyhub_fridge.auth.SamsungAccountAuth"
    ) as SamsungCls:
        SamsungCls.return_value.login = MagicMock(return_value=fake_creds)
        ok = await hub.async_relogin_samsung()

    assert ok is True
    assert hub.token == "fresh-samsung-token"
    assert hub._headers["Authorization"] == "Bearer fresh-samsung-token"
    callback.assert_called_once_with("fresh-samsung-token")


async def test_async_relogin_samsung_no_credentials_returns_false(hub):
    ok = await hub.async_relogin_samsung()
    assert ok is False


async def test_async_relogin_samsung_swallows_exceptions(hub):
    hub.attach_samsung_credentials(
        email="me", password="p", signin_client_id="s", signin_client_secret="s"
    )
    with patch(
        "custom_components.samsung_familyhub_fridge.auth.SamsungAccountAuth"
    ) as SamsungCls:
        SamsungCls.return_value.login = MagicMock(side_effect=RuntimeError("boom"))
        ok = await hub.async_relogin_samsung()

    assert ok is False
    # Token unchanged
    assert hub.token == ""


# ---------------------------------------------------------------------------
# Coordinator 401 recovery
# ---------------------------------------------------------------------------

async def test_coordinator_retries_relogin_on_401(hub, hass):
    """On 401, Samsung-mode hubs try to re-login and silently return."""
    hub.token = "stale"
    hub.attach_samsung_credentials(
        email="me", password="p", signin_client_id="s", signin_client_secret="s"
    )

    # Fake API call that always raises AuthenticationError on the first poll
    def raise_401():
        raise AuthenticationError("401")

    hub.get_current_device_status = raise_401

    fake_creds = MagicMock()
    fake_creds.access_token = "recovered-samsung-token"
    with patch(
        "custom_components.samsung_familyhub_fridge.auth.SamsungAccountAuth"
    ) as SamsungCls:
        SamsungCls.return_value.login = MagicMock(return_value=fake_creds)
        coordinator = DataCoordinator(hass, hub)
        # Should NOT raise ConfigEntryAuthFailed — relogin recovered the auth
        await coordinator._async_update_data()

    assert hub.token == "recovered-samsung-token"


async def test_coordinator_pat_mode_still_surfaces_401(hub, hass):
    """PAT-mode (no Samsung credentials) surfaces ConfigEntryAuthFailed as before."""
    hub.token = "stale-pat"

    def raise_401():
        raise AuthenticationError("401")

    hub.get_current_device_status = raise_401
    coordinator = DataCoordinator(hass, hub)

    from tests.conftest import _ConfigEntryAuthFailed
    with pytest.raises(_ConfigEntryAuthFailed):
        await coordinator._async_update_data()
