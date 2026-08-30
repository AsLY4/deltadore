"""Climate platform — thermostats, zone heaters, and the whole-home
heating control (TS-ComfortSecurityMapping)."""

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CLIMATE_USAGES, DOMAIN
from .entity import DeltaDoreEntity

_DEFAULT_ZONE_PRESETS = ["ANTI_FROST", "ECO", "COMFORT"]


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities = []
    zone_devices = []
    for device in hub.devices.values():
        if device.usage not in CLIMATE_USAGES:
            continue
        keys = set(device.metadata) | set(device.data)
        if "hvacMode" in keys:
            entities.append(DeltaDoreClimate(hub, device))
        elif "authorization" in keys or "thermicLevel" in keys:
            entities.append(DeltaDoreZoneClimate(hub, device))
            zone_devices.append(device)
        # Bare probe endpoints are exposed by the sensor platform.
    if zone_devices:
        entities.append(DeltaDoreHomeHeating(hub, zone_devices))
    # Thermal zones (areas) whose setpoint is writable → a thermostat.
    for area in hub.areas.values():
        if area.metadata.get("setpoint", {}).get("permission") in ("w", "rw"):
            entities.append(DeltaDoreAreaClimate(hub, area))
    async_add_entities(entities)


class DeltaDoreClimate(DeltaDoreEntity, ClimateEntity):
    """Heater: hvacMode NORMAL/STOP, setpoint target temperature."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "climate")
        setpoint_meta = device.metadata.get("setpoint", {})
        if "min" in setpoint_meta:
            self._attr_min_temp = float(setpoint_meta["min"])
        if "max" in setpoint_meta:
            self._attr_max_temp = float(setpoint_meta["max"])

    @property
    def hvac_mode(self) -> HVACMode:
        stopped = self._device.data.get("hvacMode") == "STOP"
        return HVACMode.OFF if stopped else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode is HVACMode.OFF:
            return HVACAction.OFF
        current = self.current_temperature
        target = self.target_temperature
        if current is None or target is None:
            return None
        return HVACAction.HEATING if current < target else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        value = self._device.data.get(
            "temperature", self._device.data.get("ambientTemperature")
        )
        return None if value is None else float(value)

    @property
    def target_temperature(self) -> float | None:
        value = self._device.data.get("setpoint")
        return None if value is None else float(value)

    async def async_set_temperature(self, **kwargs) -> None:
        await self._hub.async_send_command(
            self._device, "setpoint", float(kwargs[ATTR_TEMPERATURE])
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        value = "STOP" if hvac_mode is HVACMode.OFF else "NORMAL"
        await self._hub.async_send_command(self._device, "hvacMode", value)


class DeltaDoreZoneClimate(DeltaDoreEntity, ClimateEntity):
    """Zone-authorization heater (authorization/thermicLevel, no hvacMode).

    Zones are driven individually through thermicLevel levels
    (STOP/ANTI_FROST/ECO/COMFORT, exposed as presets); the home-level
    channel belongs to DeltaDoreHomeHeating
    (TS-ComfortSecurityMapping, variant B).
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "climate")
        keys = set(device.metadata) | set(device.data)
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        self._has_setpoint = bool({"setpoint", "heatSetpoint"} & keys)
        if self._has_setpoint:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        enum_values = device.metadata.get("thermicLevel", {}).get("enum_values", [])
        presets = [value for value in enum_values if value and value != "STOP"]
        self._attr_preset_modes = presets or list(_DEFAULT_ZONE_PRESETS)
        features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features
        minimum = device.data.get("minSetpoint", device.data.get("minHeatSetpoint"))
        maximum = device.data.get("maxSetpoint", device.data.get("maxHeatSetpoint"))
        if minimum is not None:
            self._attr_min_temp = float(minimum)
        if maximum is not None:
            self._attr_max_temp = float(maximum)

    @property
    def preset_mode(self) -> str | None:
        level = self._device.data.get("thermicLevel")
        return level if level in (self._attr_preset_modes or []) else None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._hub.async_send_command(self._device, "thermicLevel", preset_mode)

    @property
    def hvac_mode(self) -> HVACMode:
        thermic_level = self._device.data.get("thermicLevel")
        if thermic_level == "STOP":
            return HVACMode.OFF
        if thermic_level in (None, "") and (
            self._device.data.get("authorization") == "STOP"
        ):
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode is HVACMode.OFF:
            return HVACAction.OFF
        current = self.current_temperature
        target = self.target_temperature
        if current is None or target is None:
            return None
        return HVACAction.HEATING if current < target else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        value = self._device.data.get(
            "temperature", self._device.data.get("ambientTemperature")
        )
        return None if value is None else float(value)

    @property
    def target_temperature(self) -> float | None:
        value = self._device.data.get("setpoint", self._device.data.get("heatSetpoint"))
        return None if value is None else float(value)

    async def async_set_temperature(self, **kwargs) -> None:
        await self._hub.async_send_command(
            self._device, "setpoint", float(kwargs[ATTR_TEMPERATURE])
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode is HVACMode.OFF:
            await self._hub.async_send_command(self._device, "thermicLevel", "STOP")
            return
        # Zones are driven individually; the home channel is reserved for
        # the whole-home entity.
        await self._hub.async_send_command(self._device, "thermicLevel", "COMFORT")


class DeltaDoreAreaClimate(DeltaDoreEntity, ClimateEntity):
    """A thermal zone's thermostat (TS-AreasZonesMapping, writable).

    Current temperature = ambientTemperature; target = setpoint
    (bounds from /areas/meta); heat/off via thermicLevel. Writes go to
    PUT /areas/{id}/data and are verified by a read-back.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hub, area) -> None:
        super().__init__(hub, area, "climate")
        setpoint_meta = area.metadata.get("setpoint", {})
        self._attr_min_temp = float(setpoint_meta.get("min", 1.0))
        self._attr_max_temp = float(setpoint_meta.get("max", 50.0))
        self._attr_target_temperature_step = float(setpoint_meta.get("step", 0.5))
        levels = area.metadata.get("thermicLevel", {}).get("enum_values", [])
        self._heat_value = next((v for v in levels if v != "STOP"), "ANTI_FROST")

    @property
    def hvac_mode(self) -> HVACMode:
        stopped = self._device.data.get("thermicLevel") == "STOP"
        return HVACMode.OFF if stopped else HVACMode.HEAT

    def _authorized_to_heat(self) -> bool:
        """A zone setpoint only takes effect when the whole-home heating
        is on (hardware truth): the zone's own authorization if reported,
        else the hub's whole-home mode; unknown → assumed authorized."""
        auth = self._device.data.get("authorization")
        if auth is not None:
            return auth == "HEATING"
        return self._hub.home_hvac_mode in (None, "HEATING")

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode is HVACMode.OFF:
            return HVACAction.OFF
        if not self._authorized_to_heat():
            return HVACAction.IDLE  # set to heat, but "General" is off
        current = self.current_temperature
        target = self.target_temperature
        if current is None or target is None:
            return None
        return HVACAction.HEATING if current < target else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        value = self._device.data.get("ambientTemperature")
        return None if value is None else float(value)

    @property
    def target_temperature(self) -> float | None:
        value = self._device.data.get("setpoint")
        return None if value is None else float(value)

    async def async_set_temperature(self, **kwargs) -> None:
        await self._hub.async_write_area(
            self._device, "setpoint", float(kwargs[ATTR_TEMPERATURE])
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        value = "STOP" if hvac_mode is HVACMode.OFF else self._heat_value
        await self._hub.async_write_area(self._device, "thermicLevel", value)


class DeltaDoreHomeHeating(ClimateEntity):
    """Whole-home heating control, on the gateway device (UoR decision).

    PUT /home/hvac/data drives every zone at once; the state follows the
    gateway's /events/home/hvac announcements (authoritative), falling
    back to "any zone reports authorization == HEATING" until the first
    announcement arrives.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "general"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hub, zone_devices) -> None:
        self._hub = hub
        self._zones = zone_devices
        self._attr_unique_id = f"{hub.mac}_home_heating"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.mac)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._hub.add_home_hvac_listener(self.async_write_ha_state)
        )
        for zone in self._zones:
            self.async_on_remove(zone.add_listener(self.async_write_ha_state))

    @property
    def hvac_mode(self) -> HVACMode:
        mode = self._hub.home_hvac_mode
        if mode is not None:
            return HVACMode.HEAT if mode == "HEATING" else HVACMode.OFF
        heating = any(
            zone.data.get("authorization") == "HEATING" for zone in self._zones
        )
        return HVACMode.HEAT if heating else HVACMode.OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = "STOP" if hvac_mode is HVACMode.OFF else "HEATING"
        await self._hub.async_set_home_hvac_mode(mode)
