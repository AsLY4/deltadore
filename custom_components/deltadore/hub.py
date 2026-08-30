"""Gateway session supervisor and message router.

Implements TS-GatewaySessionLifecycle and the runtime side of
TS-TydomGatewayMessages / TS-CorePlatformsMapping: opens the session when
the entry loads, synchronizes the device inventory, routes responses by
Transac-Id and unsolicited pushes to device records, reconnects after
drops, hands over to reauthentication when credentials are rejected, and
closes the session on unload.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytydom import (
    TydomAuthenticationError,
    TydomConnectionError,
    gateway,
    protocol,
)

from . import const


class GatewayConnectionError(HomeAssistantError, TydomConnectionError):
    """Runtime gateway communication failure.

    Both a HomeAssistantError (a clean message reaches the UI when raised
    from a service call) and a TydomConnectionError (so the library's and
    the integration's connection failures are caught by one `except`).
    """


_LOGGER = logging.getLogger(__name__)

_SINGLE_DEVICE_RE = re.compile(r"^/devices/(\d+)/endpoints/(\d+)/data$")

# Request-style state broadcasts some gateways (Tywell PRO) send at
# connect; informational only — quieted, not "Unhandled frame".
_INFORMATIONAL_BROADCASTS = {
    "/groups/list",
    "/devices/access",
    "/devices/meta",
    "/areas/access",
}


class GatewayHub:
    """Owns the websocket session of one configured Tydom Gateway."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._rx_buffer = b""
        # Cloud mode prefixes every outbound frame with 0x02.
        remote = entry.data[const.CONF_CONFIG_MODE] == const.MODE_CLOUD
        self._tx_prefix = b"\x02" if remote else b""
        self.devices: dict[str, protocol.TydomDevice] = {}
        self.areas: dict[str, protocol.TydomDevice] = {}
        self._area_names: dict[int, str] = {}
        self._area_meta: dict[int, dict] = {}
        self._area_sensor_adder = None
        self._area_sensor_seen: set[tuple[str, str]] = set()
        self.scenarios: list[dict] = []
        self.gateway_info: dict = {}
        self._ignored: set[str] = set()
        self._last_transac_ms = 0
        # Whole-home heating channel (/events/home/hvac announcements).
        self.home_hvac_mode: str | None = None
        self.home_hvac_support: list = []
        self._home_hvac_listeners: list = []
        # Gateway-info consumers (absence entities, firmware update).
        self._info_listeners: list = []
        # Energy dests already reported as answering without a counter.
        self._energy_silent: set[tuple[str, str]] = set()

    @property
    def mac(self) -> str:
        return self._entry.data[const.CONF_MAC]

    @property
    def alarm_pin(self) -> str:
        return self._entry.data.get(const.CONF_PIN, "")

    async def async_start(self) -> None:
        """Open the session, sync the inventory; called at entry load."""
        try:
            self._ws = await self._async_open_session()
        except TydomAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Gateway rejected the stored credentials"
            ) from err
        except TydomConnectionError as err:
            raise ConfigEntryNotReady("Tydom Gateway is unreachable") from err
        self._register_gateway_device()
        self._task = self._entry.async_create_background_task(
            self._hass, self._supervise(), name="deltadore-gateway-session"
        )
        try:
            await self._async_sync_inventory()
        except TydomConnectionError as err:
            # Leave nothing behind on a failed setup: HA will retry later.
            await self.async_stop()
            raise ConfigEntryNotReady("Gateway inventory unavailable") from err
        if any(
            device.energy_dests or device.energy_srcs or device.energy_units
            for device in self.devices.values()
        ):
            self._entry.async_create_background_task(
                self._hass, self._poll_energy(), name="deltadore-energy-poll"
            )
        self._entry.async_create_background_task(
            self._hass, self._keepalive(), name="deltadore-keepalive"
        )
        self._entry.async_create_background_task(
            self._hass, self._info_refresh(), name="deltadore-info-refresh"
        )

    async def _keepalive(self) -> None:
        """Real gateways drop a silent session (~60 s observed on
        hardware): ping forever; the supervisor owns recovery."""
        while True:
            await asyncio.sleep(const.KEEPALIVE_INTERVAL)
            await self._async_send_only("GET", "/ping")

    async def async_stop(self) -> None:
        """Close the session; called at entry unload. No reconnection after."""
        if self._task is not None:
            self._task.cancel()
        self._fail_pending()
        if self._ws is not None:
            await self._ws.close()

    async def async_send_command(
        self, device: protocol.TydomDevice, name: str, value
    ) -> None:
        """Write one command; state changes only on gateway confirmation."""
        await self._async_request(
            "PUT",
            f"/devices/{device.device_id}/endpoints/{device.endpoint_id}/data",
            json.dumps([{"name": name, "value": str(value)}]),
        )

    async def async_set_home_hvac_mode(self, mode: str) -> None:
        """Home-level zone-heating mode (TS-ComfortSecurityMapping)."""
        await self._async_request("PUT", "/home/hvac/data", json.dumps({"mode": mode}))

    async def async_send_alarm_command(
        self, device: protocol.TydomDevice, value: str, pin: str
    ) -> None:
        """Total alarm command — arm away / disarm / panic / ack
        (TS-ComfortSecurityMapping)."""
        await self._async_request(
            "PUT",
            f"/devices/{device.device_id}/endpoints/{device.endpoint_id}"
            "/cdata?name=alarmCmd",
            json.dumps({"value": value, "pwd": pin}),
        )

    async def async_send_alarm_zone_command(
        self, device: protocol.TydomDevice, value: str, pin: str, zones: list[int]
    ) -> None:
        """Partial alarm command over a zone list (TS-ComfortSecurityMapping)."""
        await self._async_request(
            "PUT",
            f"/devices/{device.device_id}/endpoints/{device.endpoint_id}"
            "/cdata?name=zoneCmd",
            json.dumps({"value": value, "pwd": pin, "zones": zones}),
        )

    def alarm_zones(self, mode: str) -> list[int]:
        """Configured Tyxal zone list for an HA arm mode (config-options)."""
        key = {
            "home": const.CONF_ZONES_HOME,
            "night": const.CONF_ZONES_NIGHT,
            "custom": const.CONF_ZONES_CUSTOM,
        }[mode]
        return list(self._entry.options.get(key, []))

    async def _async_open_session(self) -> aiohttp.ClientWebSocketResponse:
        data = self._entry.data
        mode = data[const.CONF_CONFIG_MODE]
        verify_tls = mode == const.MODE_CLOUD and const.GATEWAY_TLS_VERIFY
        return await gateway.async_open_session(
            async_get_clientsession(self._hass),
            data[const.CONF_HOST],
            data[const.CONF_MAC],
            data[const.CONF_TYDOM_PASSWORD],
            verify_tls,
            port=const.GATEWAY_PORT,
            timeout=const.REQUEST_TIMEOUT,
            heartbeat=const.KEEPALIVE_INTERVAL,
        )

    def _register_gateway_device(self) -> None:
        dr.async_get(self._hass).async_get_or_create(
            config_entry_id=self._entry.entry_id,
            identifiers={(const.DOMAIN, self.mac)},
            name=f"Tydom-{self.mac[6:]}",
            manufacturer="Delta Dore",
        )

    def _next_transac_id(self) -> str:
        """Epoch milliseconds, strictly monotonic (TS-TydomGatewayMessages:
        the gateway's deferred cdata echoes only work with ms-sized ids)."""
        now_ms = time.time_ns() // 1_000_000
        self._last_transac_ms = max(now_ms, self._last_transac_ms + 1)
        return str(self._last_transac_ms)

    async def _async_request(self, method: str, path: str, body: str = ""):
        """Send one request and await its Transac-Id-correlated response."""
        if self._ws is None or self._ws.closed:
            # Fail fast with a user-facing message; no command queuing.
            raise GatewayConnectionError(
                "The Tydom gateway is reconnecting; try again shortly"
            )
        transac_id = self._next_transac_id()
        future = self._hass.loop.create_future()
        self._pending[transac_id] = future
        try:
            await self._ws.send_bytes(
                self._tx_prefix + protocol.build_request(method, path, transac_id, body)
            )
            async with asyncio.timeout(const.REQUEST_TIMEOUT):
                return await future
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise GatewayConnectionError("Gateway did not answer the request") from err
        finally:
            self._pending.pop(transac_id, None)

    def add_info_listener(self, listener):
        """Register a gateway-info callback; returns its remover."""
        self._info_listeners.append(listener)

        def _remove():
            self._info_listeners.remove(listener)

        return _remove

    async def _refresh_info(self) -> None:
        """Re-read /info and notify its consumers."""
        self.gateway_info = await self._async_request("GET", "/info") or {}
        for listener in list(self._info_listeners):
            listener()

    async def _info_refresh(self) -> None:
        """Absence and firmware state follow app-side changes
        (TS-AwayModeMapping)."""
        while True:
            await asyncio.sleep(const.INFO_REFRESH_INTERVAL)
            try:
                await self._refresh_info()
            except TydomConnectionError as err:
                _LOGGER.debug("Periodic info refresh failed: %s", err)

    @property
    def absence_to(self) -> int:
        """Global program suspension: 0 active, -1 indefinite,
        pseudo-epoch = suspended until (TS-AwayModeMapping)."""
        suspend = (self.gateway_info.get("moments") or {}).get("suspend") or {}
        to = suspend.get("to")
        return to if isinstance(to, int) else 0

    def gateway_utc_offset(self) -> timedelta:
        """The gateway clock offset, from /info (never the HA timezone)."""
        clock = (self.gateway_info.get("clock") or {}).get("clock")
        try:
            return datetime.fromisoformat(clock).utcoffset() or timedelta()
        except (TypeError, ValueError):
            return timedelta()  # fallback: treat the pseudo-epoch as UTC

    async def async_set_absence(self, to: int) -> None:
        """Write the global suspension and VERIFY the gateway applied it
        (the channel is a hardware hypothesis — TS-AwayModeMapping)."""
        await self._async_request("PUT", "/moments/suspend", json.dumps({"to": to}))
        await self._refresh_info()
        if self.absence_to != to:
            raise HomeAssistantError(
                "The gateway did not apply the absence change (global"
                " suspension channel unsupported by this firmware?)"
            )

    async def _async_sync_inventory(self) -> None:
        """Fetch configs, metadata, and state; build the device records."""
        await self._refresh_info()
        configs_payload = await self._async_request("GET", "/configs/file")
        configs = protocol.parse_configs(configs_payload)
        self.scenarios = protocol.parse_scenarios(configs_payload)
        self._area_names = protocol.parse_area_names(configs_payload)
        meta_payload = await self._async_request("GET", "/devices/meta")
        meta = protocol.parse_meta(meta_payload)
        area_links = protocol.parse_area_links(meta_payload)
        cmeta = protocol.parse_cmeta(await self._async_request("GET", "/devices/cmeta"))
        data = protocol.parse_devices_data(
            await self._async_request("GET", "/devices/data")
        )
        for key, entry in configs.items():
            if entry["usage"] not in const.SUPPORTED_USAGES:
                if key not in self._ignored:
                    self._ignored.add(key)
                    _LOGGER.debug(
                        "Ignoring device %s (%s): unsupported usage %s",
                        key,
                        entry["name"],
                        entry["usage"],
                    )
                continue
            device = self.devices.get(key)
            if device is None:
                advertised = cmeta.get(key, {})
                device = protocol.TydomDevice(
                    key=key,
                    device_id=entry["device_id"],
                    endpoint_id=entry["endpoint_id"],
                    name=entry["name"],
                    usage=entry["usage"],
                    metadata=meta.get(key, {}),
                    energy_dests=advertised.get("dests", []),
                    energy_srcs=advertised.get("srcs", []),
                    energy_units=advertised.get("units", []),
                )
                # Group the thermostat under its zone (Q67): suggested_area
                # from the /devices/meta link, no-op when absent.
                linked_area = area_links.get(key)
                if linked_area is not None:
                    device.area_name = self._area_names.get(
                        linked_area, f"Zone {linked_area}"
                    )
                self.devices[key] = device
            device.apply(data.get(key, {}))
        # Hardware diagnosability: full per-device dump (UoR instruction).
        for device in self.devices.values():
            _LOGGER.debug(
                "Device %s (%s) usage=%s data=%s metadata=%s"
                " energy_dests=%s energy_srcs=%s energy_units=%s",
                device.name,
                device.key,
                device.usage,
                device.data,
                sorted(device.metadata),
                device.energy_dests,
                device.energy_srcs,
                device.energy_units,
            )
        _LOGGER.debug(
            "Gateway info: %s; %d scenario(s)",
            self.gateway_info,
            len(self.scenarios),
        )
        # Supervised sensors report fresh values only when solicited.
        await self._async_send_only("POST", "/refresh/all")
        # Away-mode discovery (TS-AwayModeMapping): tolerant capture of
        # the program list — never fails the inventory.
        try:
            moments = await self._async_request("GET", "/moments/file")
            _LOGGER.debug("Moments file (away-mode discovery): %s", moments)
        except TydomConnectionError as err:
            _LOGGER.debug("Moments file unavailable: %s", err)
        await self._sync_areas()

    def _ensure_area(self, area_id: int) -> protocol.TydomDevice:
        """Get or create the record for a thermal zone."""
        key = f"area_{area_id}"
        area = self.areas.get(key)
        if area is None:
            area = protocol.TydomDevice(
                key=key,
                device_id=area_id,
                endpoint_id=area_id,
                name=self._area_names.get(area_id, f"Zone {area_id}"),
                usage="area",
                metadata=self._area_meta.get(area_id, {}),
            )
            self.areas[key] = area
        return area

    async def _sync_areas(self) -> None:
        """Populate thermal zones (TS-AreasZonesMapping). Tolerant: never
        fails the inventory. The meta is fetched FIRST so the zone
        records carry the setpoint bounds needed by their climate."""
        for path, label in (("/areas/meta", "meta"), ("/areas/cmeta", "cmeta")):
            try:
                body = await self._async_request("GET", path)
                _LOGGER.debug("Areas %s (discovery): %s", label, body)
                if label == "meta":
                    self._area_meta = protocol.parse_areas_meta(body)
            except TydomConnectionError as err:
                _LOGGER.debug("Areas %s unavailable: %s", label, err)
        try:
            snapshot = await self._async_request("GET", "/areas/data")
        except TydomConnectionError as err:
            _LOGGER.debug("Areas snapshot unavailable: %s", err)
            snapshot = None
        for area_id, values in protocol.parse_areas_data(snapshot).items():
            self._ensure_area(area_id).apply(values)

    async def async_write_area(self, area, name: str, value) -> None:
        """Write one zone attribute and VERIFY the gateway applied it by
        reading /areas/data back (the PUT field name is a hardware
        hypothesis — TS-AreasZonesMapping)."""
        await self._async_request(
            "PUT",
            f"/areas/{area.device_id}/data",
            json.dumps([{"name": name, "value": value}]),
        )
        try:
            snapshot = await self._async_request("GET", "/areas/data")
        except TydomConnectionError as err:
            raise HomeAssistantError(
                "The gateway did not confirm the zone change"
            ) from err
        for area_id, values in protocol.parse_areas_data(snapshot).items():
            target = self.areas.get(f"area_{area_id}")
            if target is not None:
                target.apply(values)
        applied = area.data.get(name)
        if applied != value and str(applied) != str(value):
            raise HomeAssistantError(
                f"The gateway did not apply the zone change"
                f" (unsupported field '{name}'?)"
            )

    def set_area_sensor_adder(self, adder) -> None:
        """The sensor platform registers its AddEntitiesCallback so new
        zones/attributes can be materialized on the fly."""
        self._area_sensor_adder = adder

    def note_area_sensor(self, key: str, attr: str) -> None:
        """Mark an area sensor already created (avoids duplicates)."""
        self._area_sensor_seen.add((key, attr))

    def _apply_areas_data(self, payload) -> None:
        """Apply a /areas/data frame; create the zone and its sensors on
        the fly when a zone or attribute is seen for the first time
        (TS-AreasZonesMapping — no reload needed)."""
        for area_id, values in protocol.parse_areas_data(payload).items():
            area = self._ensure_area(area_id)
            fresh = [
                attr
                for attr in values
                if (area.key, attr) not in self._area_sensor_seen
            ]
            area.apply(values)
            if fresh and self._area_sensor_adder is not None:
                self._area_sensor_adder(area, fresh)
                self._area_sensor_seen.update((area.key, attr) for attr in fresh)
            _LOGGER.debug("Area data applied to %s: %s", area.name, values)

    def _apply_info_push(self, payload) -> None:
        """A spontaneous request-style /info broadcast is applied like a
        refresh (TS-GatewaySessionLifecycle)."""
        if isinstance(payload, dict):
            self.gateway_info = payload
            for listener in list(self._info_listeners):
                listener()

    async def _async_send_only(self, method: str, path: str) -> None:
        """Send a request without awaiting any response (fire-and-forget)."""
        try:
            await self._ws.send_bytes(
                self._tx_prefix
                + protocol.build_request(method, path, self._next_transac_id())
            )
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.debug("Fire-and-forget %s %s failed: %s", method, path, err)

    async def _poll_energy(self) -> None:
        """Poll the advertised energy indexes and the consumption
        distribution (TS-ExtendedPlatformsMapping)."""
        while True:
            # The meter only publishes counters for a solicited cycle:
            # every cycle opens with a refresh before the polls.
            await self._async_send_only("POST", "/refresh/all")
            for device in list(self.devices.values()):
                base = (
                    f"/devices/{device.device_id}/endpoints/{device.endpoint_id}/cdata"
                )
                for dest in device.energy_dests:
                    await self._poll_energy_slot(
                        device,
                        f"dest {dest}",
                        f"{base}?name=energyIndex&dest={dest}&reset=false",
                    )
                for src in device.energy_srcs:
                    await self._poll_energy_slot(
                        device,
                        f"src {src}",
                        f"{base}?name=energyDistrib"
                        f"&period=YEAR&periodOffset=0&src={src}",
                    )
                for unit in device.energy_units:
                    await self._poll_energy_slot(
                        device,
                        f"unit {unit}",
                        f"{base}?name=energyInstant&unit={unit}&reset=false",
                    )
            await asyncio.sleep(const.ENERGY_POLL_INTERVAL)

    async def _poll_energy_slot(self, device, slot: str, path: str) -> None:
        """One energy poll; counters may come inline or as cdata echoes."""
        try:
            payload = await self._async_request("GET", path)
        except TydomConnectionError:
            return  # retried at the next tick
        values_by_key = protocol.parse_energy_cdata(payload)
        if not values_by_key:
            # Real meters acknowledge every poll headers-only; served
            # slots deliver via /devices/cdata echoes, unserved ones
            # never answer. Log once per slot.
            silent = (device.key, slot)
            if silent not in self._energy_silent:
                self._energy_silent.add(silent)
                _LOGGER.debug(
                    "Energy poll for %s %s returned no counter (served"
                    " slots arrive as cdata echoes; unserved ones stay"
                    " unknown; not logged again): %r",
                    device.name,
                    slot,
                    str(payload)[:200],
                )
        for key, values in values_by_key.items():
            target = self.devices.get(key)
            if target is not None:
                target.apply(values)
                _LOGGER.debug(
                    "Energy data applied to %s (%s): %s",
                    target.name,
                    key,
                    values,
                )

    async def async_activate_scenario(self, scenario_id: int) -> None:
        """Launch a gateway scenario (bare PUT, no body)."""
        await self._async_request("PUT", f"/scenarios/{scenario_id}")

    async def async_trigger_gateway_update(self) -> None:
        """Trigger the gateway firmware update (bare PUT, no body)."""
        await self._async_request("PUT", "/configs/gateway/update")

    async def _supervise(self) -> None:
        """Route frames while the entry is loaded; reconnect after drops."""
        while True:
            async for msg in self._ws:
                raw = (
                    bytes(msg.data)
                    if isinstance(msg.data, (bytes, bytearray))
                    else str(msg.data).encode()
                )
                self._receive_bytes(raw.lstrip(b"\x02"))

            # Session dropped: reconnect until it works, or stop for reauth.
            _LOGGER.debug("Gateway session dropped; reconnecting")
            self._rx_buffer = b""
            self._fail_pending()
            while True:
                await asyncio.sleep(const.RECONNECT_INTERVAL)
                try:
                    self._ws = await self._async_open_session()
                except TydomAuthenticationError:
                    _LOGGER.debug("Reconnect rejected; starting reauthentication")
                    self._entry.async_start_reauth(self._hass)
                    return
                except TydomConnectionError:
                    continue
                _LOGGER.debug("Gateway session re-established")
                self._entry.async_create_background_task(
                    self._hass,
                    self._async_sync_inventory(),
                    name="deltadore-inventory-sync",
                )
                break

    def _receive_bytes(self, raw: bytes) -> None:
        """Scan the byte stream message by message (frames are arbitrary)."""
        buffer = self._rx_buffer + raw
        self._rx_buffer = b""
        while buffer:
            try:
                frame, remainder = protocol.next_frame(buffer)
            except ValueError:
                _LOGGER.debug("Ignoring malformed message: %r", buffer[:120])
                return
            if frame is None:
                if not remainder:
                    _LOGGER.debug("Ignoring non-HTTP bytes: %r", buffer[:120])
                elif len(remainder) > const.FRAME_BUFFER_LIMIT:
                    _LOGGER.debug(
                        "Dropping oversized partial message (%d bytes)",
                        len(remainder),
                    )
                else:
                    self._rx_buffer = remainder  # incomplete: wait for more
                return
            try:
                self._dispatch(frame)
            except Exception:  # noqa: BLE001 - session must survive bad frames
                _LOGGER.debug(
                    "Failed to dispatch frame for %s", frame.path, exc_info=True
                )
            buffer = remainder

    def _dispatch(self, frame: protocol.Frame) -> None:
        try:
            payload = frame.json
        except ValueError:
            _LOGGER.debug(
                "Non-JSON body for %s; treated as empty: %r",
                frame.path,
                frame.body[:200],
            )
            payload = None
        if frame.kind == "response":
            future = self._pending.get(frame.transac_id or "")
            if future is not None and not future.done():
                future.set_result(payload)
                return
        single_device = _SINGLE_DEVICE_RE.match(frame.path)
        if frame.path == "/devices/data":
            self._apply_devices_data(payload)
        elif frame.path == "/devices/cdata":
            self._apply_energy_cdata(payload)
        elif frame.path == "/events/home/hvac":
            self._apply_home_hvac(payload)
        elif frame.path == "/areas/data":
            self._apply_areas_data(payload)
        elif single_device is not None:
            # Per-endpoint delivery (supervised sensors after refresh/all):
            # flat {id, error, data} body, ids rebuilt from the path.
            if isinstance(payload, dict):
                self._apply_devices_data(
                    [
                        {
                            "id": int(single_device.group(1)),
                            "endpoints": [
                                {
                                    "id": int(single_device.group(2)),
                                    "error": payload.get("error", 0),
                                    "data": payload.get("data", []),
                                }
                            ],
                        }
                    ]
                )
        elif frame.path == "/refresh/all":
            pass  # acknowledgement of our fire-and-forget refresh
        elif frame.path == "/ping":
            pass  # acknowledgement of our keep-alive ping
        elif frame.path == "/info":
            # Spontaneous state broadcast (Tywell PRO): apply like a
            # refresh so absence/firmware follow immediately.
            self._apply_info_push(payload)
        elif frame.path in ("/areas/meta", "/areas/cmeta"):
            _LOGGER.debug(
                "Areas %s (discovery, broadcast): %s",
                frame.path.rsplit("/", 1)[-1],
                payload,
            )
        elif frame.path == "/areas/devices":
            _LOGGER.debug("Area membership (broadcast): %s", payload)
        elif frame.path in _INFORMATIONAL_BROADCASTS:
            _LOGGER.debug("Informational broadcast %s ignored", frame.path)
        else:
            _LOGGER.debug("Unhandled frame for %s: %r", frame.path, frame.body[:120])

    def add_home_hvac_listener(self, listener):
        """Register a whole-home heating callback; returns its remover."""
        self._home_hvac_listeners.append(listener)

        def _remove():
            self._home_hvac_listeners.remove(listener)

        return _remove

    def _apply_home_hvac(self, payload) -> None:
        """Whole-home heating announcement (POST /events/home/hvac)."""
        if not isinstance(payload, dict) or "mode" not in payload:
            _LOGGER.debug("Home heating announcement without mode: %r", payload)
            return
        self.home_hvac_mode = payload["mode"]
        support = payload.get("support")
        if isinstance(support, list):
            self.home_hvac_support = support
        _LOGGER.debug(
            "Home heating mode is now %s (support=%s)",
            self.home_hvac_mode,
            self.home_hvac_support,
        )
        for listener in list(self._home_hvac_listeners):
            listener()

    def _cdata_origin(self, payload) -> str:
        """Attribute a cdata frame to its device for the logs."""
        try:
            device_id = payload[0]["id"]
            endpoint_id = payload[0]["endpoints"][0]["id"]
        except (TypeError, KeyError, IndexError):
            return "unknown device"
        device = self.devices.get(f"{endpoint_id}_{device_id}")
        return device.name if device else f"{endpoint_id}_{device_id}"

    def _apply_energy_cdata(self, payload) -> None:
        """Unsolicited cdata pushes carry energy counters like poll replies."""
        values_by_key = protocol.parse_energy_cdata(payload)
        if not values_by_key:
            _LOGGER.debug(
                "Ignoring cdata frame without energy counters for %s"
                " (e.g. a destNOK echo for an unserved slot)",
                self._cdata_origin(payload),
            )
            return
        for key, values in values_by_key.items():
            device = self.devices.get(key)
            if device is not None:
                device.apply(values)
                _LOGGER.debug(
                    "Energy data applied to %s (%s): %s",
                    device.name,
                    key,
                    values,
                )

    def _apply_devices_data(self, payload) -> None:
        for key, values in protocol.parse_devices_data(payload).items():
            device = self.devices.get(key)
            if device is not None:
                device.apply(values)
                _LOGGER.debug(
                    "Data applied to %s (%s): %s",
                    device.name,
                    key,
                    values,
                )
            elif key not in self._ignored:
                # Deliberately-ignored devices push too; stay quiet for them.
                _LOGGER.debug("State push for unknown device %s", key)

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    GatewayConnectionError(
                        "The Tydom gateway is reconnecting; try again shortly"
                    )
                )
        self._pending.clear()
