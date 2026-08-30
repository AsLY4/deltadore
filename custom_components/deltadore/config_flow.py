"""Config flow for the Delta Dore integration.

Implements TS-IntegrationSetupConfigFlow: mode selection (local, cloud,
manual), field validation before any network call, cloud credential
retrieval, gateway connection validation, discovery prefill, and duplicate
prevention via unique_id = uppercased MAC.
"""

import ipaddress
import logging
import re

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from pytydom import (
    TydomAuthenticationError,
    TydomConnectionError,
    TydomError,
    cloud,
    gateway,
)

from . import const

_LOGGER = logging.getLogger(__name__)

# Masked input for secrets (PIN, passwords) — never shown in clear.
_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _parse_zones(raw: str) -> list[int]:
    """Parse a comma/space-separated zone list; raises ValueError on
    a non-integer token (config-options)."""
    return [int(token) for token in re.split(r"[,\s]+", (raw or "").strip()) if token]


MAC_PATTERN = re.compile(r"^[0-9A-Fa-f]{12}$")
EMAIL_PATTERN = re.compile(
    r"^([A-Za-z0-9]+[.\-_])*[A-Za-z0-9]+@[A-Za-z0-9\-]+(\.[A-Za-z]{2,})+$"
)
HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9\-]+$")
PIN_PATTERN = re.compile(r"^\d*$")


_RECOVERABLE_ERRORS = (TydomError,)


def _base_error(err: Exception) -> str:
    """Map a validation failure to its user-facing error key."""
    if isinstance(err, TydomAuthenticationError):
        return "authentication_error"
    if isinstance(err, TydomConnectionError):
        return "cannot_connect"
    return "communication_error"


def _cloud_kwargs() -> dict:
    """Delta Dore cloud endpoints, read at call time so the test seams
    that patch them take effect."""
    return {
        "discovery_url": const.CLOUD_DISCOVERY_URL,
        "sites_url": const.CLOUD_SITES_URL,
        "client_id": const.CLOUD_CLIENT_ID,
        "scope": const.CLOUD_SCOPE,
        "timeout": const.REQUEST_TIMEOUT,
    }


def _gateway_kwargs() -> dict:
    """Gateway transport tunables, read at call time (patchable seams)."""
    return {
        "port": const.GATEWAY_PORT,
        "timeout": const.REQUEST_TIMEOUT,
        "heartbeat": const.KEEPALIVE_INTERVAL,
    }


def _host_valid(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        labels = host.split(".")
        return bool(host) and all(HOST_LABEL_PATTERN.match(label) for label in labels)


def _validate_fields(mode: str, data: dict) -> dict:
    """Field-level validation, before any network call."""
    errors = {}
    if mode != const.MODE_CLOUD and not _host_valid(data.get(const.CONF_HOST, "")):
        errors[const.CONF_HOST] = "invalid_host"
    if not MAC_PATTERN.match(data.get(const.CONF_MAC, "")):
        errors[const.CONF_MAC] = "invalid_macaddress"
    if mode == const.MODE_MANUAL:
        if len(data.get(const.CONF_TYDOM_PASSWORD, "")) < 3:
            errors[const.CONF_TYDOM_PASSWORD] = "invalid_password"
    else:
        if not EMAIL_PATTERN.match(data.get(const.CONF_EMAIL, "")):
            errors[const.CONF_EMAIL] = "invalid_email"
        if len(data.get(const.CONF_PASSWORD, "")) < 3:
            errors[const.CONF_PASSWORD] = "invalid_password"
    if not PIN_PATTERN.match(data.get(const.CONF_PIN, "") or ""):
        errors[const.CONF_PIN] = "invalid_pin"
    return errors


class DeltaDoreConfigFlow(ConfigFlow, domain=const.DOMAIN):
    """UI setup flow for a Tydom Gateway."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_mac: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DeltaDoreOptionsFlow()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            mode = user_input[const.CONF_CONFIG_MODE]
            if mode == const.MODE_LOCAL:
                return await self.async_step_local()
            if mode == const.MODE_CLOUD:
                return await self.async_step_cloud()
            return await self.async_step_manual()
        schema = vol.Schema(
            {
                vol.Required(const.CONF_CONFIG_MODE): vol.In(
                    [const.MODE_LOCAL, const.MODE_CLOUD, const.MODE_MANUAL]
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        mac = discovery_info.macaddress.replace(":", "").upper()
        await self.async_set_unique_id(mac)
        existing = next(
            (
                entry
                for entry in self._async_current_entries()
                if entry.unique_id == mac
            ),
            None,
        )
        updates = None
        if (
            existing is not None
            and existing.data.get(const.CONF_CONFIG_MODE) != const.MODE_CLOUD
        ):
            # A gateway that changed address after a DHCP renewal is
            # followed without reconfiguration; cloud entries keep the
            # mediation host (TS-IntegrationSetupConfigFlow).
            updates = {const.CONF_HOST: discovery_info.ip}
        self._abort_if_unique_id_configured(updates=updates)
        self._discovered_host = discovery_info.ip
        self._discovered_mac = mac
        return await self.async_step_user()

    async def async_step_local(self, user_input=None) -> ConfigFlowResult:
        return await self._async_step_mode(const.MODE_LOCAL, user_input)

    async def async_step_cloud(self, user_input=None) -> ConfigFlowResult:
        return await self._async_step_mode(const.MODE_CLOUD, user_input)

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        return await self._async_step_mode(const.MODE_MANUAL, user_input)

    def _mode_schema(self, mode: str) -> vol.Schema:
        fields = {}
        if mode != const.MODE_CLOUD:
            fields[
                vol.Required(
                    const.CONF_HOST, default=self._discovered_host or vol.UNDEFINED
                )
            ] = str
        fields[
            vol.Required(const.CONF_MAC, default=self._discovered_mac or vol.UNDEFINED)
        ] = str
        if mode == const.MODE_MANUAL:
            fields[vol.Required(const.CONF_TYDOM_PASSWORD)] = _SECRET
        else:
            fields[vol.Required(const.CONF_EMAIL)] = str
            fields[vol.Required(const.CONF_PASSWORD)] = _SECRET
        fields[vol.Optional(const.CONF_PIN, default="")] = _SECRET
        return vol.Schema(fields)

    async def _async_step_mode(self, mode: str, user_input) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            errors = _validate_fields(mode, user_input)
            if not errors:
                try:
                    return await self._async_validate_and_create(mode, user_input)
                except _RECOVERABLE_ERRORS as err:
                    _LOGGER.debug(
                        "Setup validation failed in %s mode", mode, exc_info=err
                    )
                    errors["base"] = _base_error(err)
        return self.async_show_form(
            step_id=mode, data_schema=self._mode_schema(mode), errors=errors
        )

    async def _async_validate_and_create(self, mode, user_input) -> ConfigFlowResult:
        mac = user_input[const.CONF_MAC].upper()
        host = (
            const.MEDIATION_HOST
            if mode == const.MODE_CLOUD
            else user_input[const.CONF_HOST]
        )
        session = async_get_clientsession(self.hass)

        if mode == const.MODE_MANUAL:
            gateway_password = user_input[const.CONF_TYDOM_PASSWORD]
        else:
            gateway_password = await cloud.async_get_gateway_password(
                session,
                user_input[const.CONF_EMAIL],
                user_input[const.CONF_PASSWORD],
                mac,
                **_cloud_kwargs(),
            )

        verify_tls = mode == const.MODE_CLOUD and const.GATEWAY_TLS_VERIFY
        await gateway.async_validate_connection(
            session,
            host,
            mac,
            gateway_password,
            verify_tls,
            remote=mode == const.MODE_CLOUD,
            **_gateway_kwargs(),
        )

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        data = {
            const.CONF_CONFIG_MODE: mode,
            const.CONF_HOST: host,
            const.CONF_MAC: mac,
            const.CONF_TYDOM_PASSWORD: gateway_password,
            const.CONF_PIN: user_input.get(const.CONF_PIN, ""),
        }
        if mode != const.MODE_MANUAL:
            # Kept for reauthentication: account modes re-retrieve the
            # gateway password from the cloud (TS-GatewaySessionLifecycle).
            data[const.CONF_EMAIL] = user_input[const.CONF_EMAIL]
        return self.async_create_entry(title=f"Tydom-{mac[6:]}", data=data)

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Ask again for the credential that can expire, then reload."""
        entry = self._get_reauth_entry()
        mode = entry.data[const.CONF_CONFIG_MODE]
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                if mode == const.MODE_MANUAL:
                    gateway_password = user_input[const.CONF_TYDOM_PASSWORD]
                else:
                    gateway_password = await cloud.async_get_gateway_password(
                        session,
                        entry.data[const.CONF_EMAIL],
                        user_input[const.CONF_PASSWORD],
                        entry.data[const.CONF_MAC],
                        **_cloud_kwargs(),
                    )
                verify_tls = mode == const.MODE_CLOUD and const.GATEWAY_TLS_VERIFY
                await gateway.async_validate_connection(
                    session,
                    entry.data[const.CONF_HOST],
                    entry.data[const.CONF_MAC],
                    gateway_password,
                    verify_tls,
                    remote=mode == const.MODE_CLOUD,
                    **_gateway_kwargs(),
                )
            except _RECOVERABLE_ERRORS as err:
                _LOGGER.debug("Reauthentication validation failed", exc_info=err)
                errors["base"] = _base_error(err)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={const.CONF_TYDOM_PASSWORD: gateway_password},
                )
        field = (
            const.CONF_TYDOM_PASSWORD
            if mode == const.MODE_MANUAL
            else const.CONF_PASSWORD
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(field): _SECRET}),
            errors=errors,
        )


class DeltaDoreOptionsFlow(OptionsFlow):
    """Post-setup reconfiguration (config-options): change the gateway
    host, set the Alarm PIN, and declare the Tyxal alarm zone lists."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        entry = self.config_entry
        mode = entry.data[const.CONF_CONFIG_MODE]
        errors = {}
        if user_input is not None:
            errors = self._validate(mode, user_input)
            if not errors:
                new_data = {
                    **entry.data,
                    const.CONF_PIN: user_input.get(const.CONF_PIN, ""),
                }
                if mode != const.MODE_CLOUD:
                    new_data[const.CONF_HOST] = user_input[const.CONF_HOST]
                new_options = {
                    const.CONF_ZONES_HOME: _parse_zones(
                        user_input.get(const.CONF_ZONES_HOME, "")
                    ),
                    const.CONF_ZONES_NIGHT: _parse_zones(
                        user_input.get(const.CONF_ZONES_NIGHT, "")
                    ),
                    const.CONF_ZONES_CUSTOM: _parse_zones(
                        user_input.get(const.CONF_ZONES_CUSTOM, "")
                    ),
                }
                old_data = dict(entry.data)
                old_options = dict(entry.options)
                host_changed = mode != const.MODE_CLOUD and new_data.get(
                    const.CONF_HOST
                ) != old_data.get(const.CONF_HOST)
                self.hass.config_entries.async_update_entry(
                    entry, data=new_data, options=new_options
                )
                # The PIN is read live from the entry and the zones are
                # inert, so only a HOST change needs a reconnection. A
                # reload closes the current session BEFORE reopening it,
                # so the reconnection validates the new host with NO
                # competing connection — real gateways accept only one
                # session at a time (hardware truth 2026-07-21, #40).
                if host_changed:
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    if entry.state is not ConfigEntryState.LOADED:
                        # Unreachable: restore the previous configuration.
                        self.hass.config_entries.async_update_entry(
                            entry, data=old_data, options=old_options
                        )
                        await self.hass.config_entries.async_reload(entry.entry_id)
                        errors["base"] = "cannot_connect"
                if not errors:
                    return self.async_create_entry(title="", data=new_options)
        return self.async_show_form(
            step_id="init", data_schema=self._schema(mode, entry), errors=errors
        )

    def _validate(self, mode: str, user_input: dict) -> dict:
        errors = {}
        if not PIN_PATTERN.match(user_input.get(const.CONF_PIN, "") or ""):
            errors[const.CONF_PIN] = "invalid_pin"
        for field in (
            const.CONF_ZONES_HOME,
            const.CONF_ZONES_NIGHT,
            const.CONF_ZONES_CUSTOM,
        ):
            try:
                _parse_zones(user_input.get(field, ""))
            except ValueError:
                errors[field] = "invalid_zones"
        if mode != const.MODE_CLOUD and not _host_valid(
            user_input.get(const.CONF_HOST, "")
        ):
            errors[const.CONF_HOST] = "invalid_host"
        return errors

    def _schema(self, mode: str, entry: ConfigEntry) -> vol.Schema:
        def zones(field):
            return ",".join(str(z) for z in entry.options.get(field, []))

        fields = {}
        if mode != const.MODE_CLOUD:
            fields[
                vol.Required(
                    const.CONF_HOST, default=entry.data.get(const.CONF_HOST, "")
                )
            ] = str
        fields[
            vol.Optional(const.CONF_PIN, default=entry.data.get(const.CONF_PIN, ""))
        ] = _SECRET
        fields[
            vol.Optional(const.CONF_ZONES_HOME, default=zones(const.CONF_ZONES_HOME))
        ] = str
        fields[
            vol.Optional(const.CONF_ZONES_NIGHT, default=zones(const.CONF_ZONES_NIGHT))
        ] = str
        fields[
            vol.Optional(
                const.CONF_ZONES_CUSTOM, default=zones(const.CONF_ZONES_CUSTOM)
            )
        ] = str
        return vol.Schema(fields)
