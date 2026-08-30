"""Sensor platform — temperature and energy measurements (read-only)."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALARM_USAGES,
    CLIMATE_USAGES,
    ENERGY_USAGES,
    INSTANT_POWER_KEY,
    PLUG_ENERGY_INDEX_KEY,
    SUN_USAGES,
    SWITCH_USAGES,
    TEMPERATURE_DEST_PREFIX,
    TEMPERATURE_USAGES,
    UNIT_MAP,
)
from .entity import DeltaDoreEntity

_ZONE_CONTROL_KEYS = {"hvacMode", "authorization", "thermicLevel"}

# Vendor-vocabulary display names for the known energy slots
# (TS-MissingSensorsMapping, "Energy sensor display names").
_SLOT_TRANSLATIONS = {
    "ELEC_TOTAL": "elec_total",
    "ELEC_HEATING": "elec_heating",
    "ELEC_HOTWATER": "elec_hotwater",
    "ELEC_OUTLET": "elec_outlet",
    "ELEC_OTHER": "elec_other",
    "TEMP_INDOOR": "temp_indoor",
    "TEMP_OUTDOOR": "temp_outdoor",
    "ELEC_A": "elec_a",
}


def _name_by_slot(sensor, slot: str, fallback: str) -> None:
    """Translated vendor name for known slots, raw fallback otherwise."""
    translation_key = _SLOT_TRANSLATIONS.get(slot)
    if translation_key is not None:
        del sensor._attr_name
        sensor._attr_translation_key = translation_key
    else:
        sensor._attr_name = fallback


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities = []
    # The gateway designates its own outdoor-temperature source in /info
    # (weather.outTempSrc); that endpoint ALWAYS gets a sensor, even
    # before it reports anything (TS-ComfortSecurityMapping).
    out_temp_src = (hub.gateway_info.get("weather") or {}).get("outTempSrc") or {}
    designated = None
    if out_temp_src.get("devId") is not None and out_temp_src.get("epId") is not None:
        designated = f"{out_temp_src['epId']}_{out_temp_src['devId']}"

    # Thermal zones (areas): one read-only sensor per reported attribute,
    # except the target owned by the zone climate when writable.
    def _area_owns_climate(area) -> bool:
        return area.metadata.get("setpoint", {}).get("permission") in ("w", "rw")

    def _climate_owned_attr(area, attr) -> bool:
        return attr in ("setpoint", "heatSetpoint") and _area_owns_climate(area)

    for area in hub.areas.values():
        for attr in sorted(area.data):
            if _climate_owned_attr(area, attr):
                continue
            entities.append(DeltaDoreAreaSensor(hub, area, attr))
            hub.note_area_sensor(area.key, attr)

    def _add_area_sensors(area, attrs):
        async_add_entities(
            DeltaDoreAreaSensor(hub, area, attr)
            for attr in attrs
            if not _climate_owned_attr(area, attr)
        )

    hub.set_area_sensor_adder(_add_area_sensors)
    for device in hub.devices.values():
        if device.usage in TEMPERATURE_USAGES:
            entities.append(DeltaDoreTemperature(hub, device))
        elif device.usage in ALARM_USAGES:
            # The Tyxal central carries an outdoor probe and per-zone state.
            if "outTemperature" in device.data or device.key == designated:
                entities.append(
                    DeltaDoreTemperature(
                        hub, device, translation_key="outdoor_temperature"
                    )
                )
            entities.extend(
                DeltaDoreAlarmZone(hub, device, zone)
                for zone in range(1, 9)
                if device.data.get(f"zone{zone}State", "UNUSED") != "UNUSED"
            )
            entities.append(DeltaDoreAlarmMode(hub, device))
        elif device.usage in ENERGY_USAGES:
            for dest in device.energy_dests:
                if dest.startswith(TEMPERATURE_DEST_PREFIX):
                    entities.append(DeltaDoreEnergyTemperature(hub, device, dest))
                else:
                    entities.append(DeltaDoreEnergyIndex(hub, device, dest))
            for unit in device.energy_units:
                entities.append(DeltaDoreInstantMeasurement(hub, device, unit))
            if INSTANT_POWER_KEY in device.data:
                entities.append(DeltaDoreInstantPower(hub, device))
        elif device.usage in SWITCH_USAGES:
            # Metering plugs report power/index through their device data.
            if INSTANT_POWER_KEY in device.data:
                entities.append(DeltaDoreInstantPower(hub, device))
            if PLUG_ENERGY_INDEX_KEY in device.data:
                entities.append(DeltaDorePlugEnergyIndex(hub, device))
        elif device.usage in SUN_USAGES:
            entities.extend(
                DeltaDoreMeasurement(hub, device, key)
                for key, value in device.data.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )
        elif device.usage in CLIMATE_USAGES:
            keys = set(device.metadata) | set(device.data)
            has_temp = {"outTemperature", "temperature", "ambientTemperature"} & keys
            if device.key == designated:
                entities.append(
                    DeltaDoreTemperature(
                        hub, device, translation_key="outdoor_temperature"
                    )
                )
            elif not (_ZONE_CONTROL_KEYS & keys) and has_temp:
                # Bare probe endpoint of a heating system.
                entities.append(DeltaDoreTemperature(hub, device))
    async_add_entities(entities)


class DeltaDoreTemperature(DeltaDoreEntity, SensorEntity):
    """Temperature from outTemperature, temperature, or ambientTemperature
    (real STI probes report the last one)."""

    _TEMPERATURE_KEYS = ("outTemperature", "temperature", "ambientTemperature")

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub, device, translation_key: str | None = None) -> None:
        super().__init__(hub, device, "sensor")
        if translation_key is not None:
            # Let the translated name apply instead of the device name.
            del self._attr_name
            self._attr_translation_key = translation_key
        unit = device.metadata.get("outTemperature", {}).get("unit", "degC")
        self._attr_native_unit_of_measurement = UNIT_MAP.get(unit, "°C")

    @property
    def native_value(self) -> float | None:
        data = self._device.data
        value = next((data[key] for key in self._TEMPERATURE_KEYS if key in data), None)
        return None if value is None else float(value)


class DeltaDoreEnergyIndex(DeltaDoreEntity, SensorEntity):
    """Consumption index polled from the gateway, one per advertised dest."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    def __init__(self, hub, device, dest: str) -> None:
        super().__init__(hub, device, f"sensor_energyIndex_{dest}")
        _name_by_slot(self, dest, f"Index {dest}")
        self._dest = dest

    @property
    def native_value(self):
        return self._device.data.get(f"energyIndex_{self._dest}")


class DeltaDoreInstantMeasurement(DeltaDoreEntity, SensorEntity):
    """A meter's instant measurement, one per advertised unit slot
    (TS-MissingSensorsMapping): the cdata echo's measure, in hundredths
    of the physical unit, is applied divided by 100."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub, device, unit_slot: str) -> None:
        super().__init__(hub, device, f"sensor_energyInstant_{unit_slot}")
        _name_by_slot(self, unit_slot, unit_slot)
        self._unit_slot = unit_slot
        if unit_slot == "ELEC_A":
            self._attr_device_class = SensorDeviceClass.CURRENT
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        elif unit_slot.endswith("P"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_value(self):
        return self._device.data.get(f"energyInstant_{self._unit_slot}")


class DeltaDoreInstantPower(DeltaDoreEntity, SensorEntity):
    """Instant power reported through the regular device data."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, f"sensor_{INSTANT_POWER_KEY}")
        del self._attr_name
        self._attr_translation_key = "instant_power"

    @property
    def native_value(self):
        return self._device.data.get(INSTANT_POWER_KEY)


class DeltaDoreEnergyTemperature(DeltaDoreEntity, SensorEntity):
    """A meter's TEMP_* destination: a temperature, not an energy index.

    Disabled by default — no known gateway serves these dests (the
    cmeta declaration is firmware-generic; UoR decision 2026-07-20).
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub, device, dest: str) -> None:
        super().__init__(hub, device, f"sensor_energyIndex_{dest}")
        _name_by_slot(self, dest, dest)
        self._dest = dest

    @property
    def native_value(self):
        return self._device.data.get(f"energyIndex_{self._dest}")


class DeltaDorePlugEnergyIndex(DeltaDoreEntity, SensorEntity):
    """A metering plug's total index, reported through device data."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, f"sensor_{PLUG_ENERGY_INDEX_KEY}")
        del self._attr_name
        self._attr_translation_key = "total_index"

    @property
    def native_value(self):
        return self._device.data.get(PLUG_ENERGY_INDEX_KEY)


_DEVICE_CLASS_BY_UNIT = {
    "W/m²": SensorDeviceClass.IRRADIANCE,
    "°C": SensorDeviceClass.TEMPERATURE,
    "W": SensorDeviceClass.POWER,
}


class DeltaDoreMeasurement(DeltaDoreEntity, SensorEntity):
    """Generic numeric measurement (sunlight sensors), one per data key."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub, device, data_key: str) -> None:
        super().__init__(hub, device, f"sensor_{data_key}")
        self._attr_name = data_key
        self._data_key = data_key
        unit = device.metadata.get(data_key, {}).get("unit")
        mapped_unit = UNIT_MAP.get(unit, unit)
        self._attr_native_unit_of_measurement = mapped_unit
        self._attr_device_class = _DEVICE_CLASS_BY_UNIT.get(mapped_unit)

    @property
    def native_value(self):
        return self._device.data.get(self._data_key)


# Thermal-zone attribute -> (translation key, device class, unit, diagnostic)
# (TS-AreasZonesMapping). Attributes outside this table are exposed with
# their raw name, string values landing in the diagnostic category.
_AREA_ATTRS = {
    "ambientTemperature": (
        "area_temperature",
        SensorDeviceClass.TEMPERATURE,
        "°C",
        False,
    ),
    "setpoint": ("area_setpoint", SensorDeviceClass.TEMPERATURE, "°C", False),
    "thermicLevel": ("area_thermic_level", None, None, True),
    "authorization": ("area_authorization", None, None, True),
}


class DeltaDoreAlarmZone(DeltaDoreEntity, SensorEntity):
    """One active alarm zone: armed / disarmed (TS-ComfortSecurityMapping).

    An ENUM sensor so its states are translated; zone 8 is the
    central's OTHER zone (hardware 2026-07-21)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["armed", "disarmed"]

    def __init__(self, hub, device, zone: int) -> None:
        super().__init__(hub, device, f"alarm_zone_{zone}")
        del self._attr_name
        if zone == 8:
            self._attr_translation_key = "alarm_zone_other"
        else:
            self._attr_translation_key = "alarm_zone"
            self._attr_translation_placeholders = {"zone": str(zone)}
        self._zone = zone

    @property
    def native_value(self):
        state = self._device.data.get(f"zone{self._zone}State")
        return {"ON": "armed", "OFF": "disarmed"}.get(state)


class DeltaDoreAlarmMode(DeltaDoreEntity, SensorEntity):
    """The alarm central's operating mode: normal / maintenance
    (TS-ComfortSecurityMapping)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normal", "maintenance"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "alarm_mode")
        del self._attr_name
        self._attr_translation_key = "alarm_operating_mode"

    @property
    def native_value(self):
        maintenance = self._device.data.get("alarmMode") == "MAINTENANCE"
        return "maintenance" if maintenance else "normal"


class DeltaDoreAreaSensor(DeltaDoreEntity, SensorEntity):
    """One read-only attribute of a thermal zone (TS-AreasZonesMapping)."""

    def __init__(self, hub, area, attr: str) -> None:
        super().__init__(hub, area, f"sensor_{attr}")
        self._attr = attr
        mapping = _AREA_ATTRS.get(attr)
        if mapping is not None:
            translation_key, device_class, unit, diagnostic = mapping
            del self._attr_name
            self._attr_translation_key = translation_key
            self._attr_device_class = device_class
            self._attr_native_unit_of_measurement = unit
            if device_class is not None:
                self._attr_state_class = SensorStateClass.MEASUREMENT
            if diagnostic:
                self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_name = attr

    @property
    def native_value(self):
        return self._device.data.get(self._attr)
