"""Weather platform — Tydom weather stations (TS-ExtendedPlatformsMapping)."""

from homeassistant.components.weather import WeatherEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import WEATHER_USAGES
from .entity import DeltaDoreEntity

_SUFFIX_CONDITIONS = (
    ("CLOUDS", "cloudy"),
    ("SHOWER_RAIN", "pouring"),
    ("RAIN", "rainy"),
    ("THUNDERSTORM", "lightning"),
    ("SNOW", "snowy"),
    ("MIST", "fog"),
)


def _map_condition(value: str | None) -> str | None:
    if not value or value == "UNAVAILABLE":
        return None
    if value == "DAY_CLEAR_SKY":
        return "sunny"
    if value == "NIGHT_CLEAR_SKY":
        return "clear-night"
    for suffix, condition in _SUFFIX_CONDITIONS:
        if value.endswith(suffix):
            return condition
    return None


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        DeltaDoreWeather(hub, device)
        for device in hub.devices.values()
        if device.usage in WEATHER_USAGES
    )


class DeltaDoreWeather(DeltaDoreEntity, WeatherEntity):
    """Outdoor conditions and temperature from a Tydom weather station."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "weather")

    @property
    def native_temperature(self) -> float | None:
        value = self._device.data.get("outTemperature")
        return None if value is None else float(value)

    @property
    def condition(self) -> str | None:
        return _map_condition(self._device.data.get("weather"))
