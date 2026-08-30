"""Light platform (TS-CorePlatformsMapping)."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import LIGHT_USAGES
from .entity import DeltaDoreEntity


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        DeltaDoreLight(hub, device)
        for device in hub.devices.values()
        if device.usage in LIGHT_USAGES
    )


class DeltaDoreLight(DeltaDoreEntity, LightEntity):
    """Light driven by its level (0-100 percent)."""

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "light")
        dimmable = device.metadata.get("level", {}).get("type") == "numeric"
        mode = ColorMode.BRIGHTNESS if dimmable else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def _level(self) -> int:
        return int(self._device.data.get("level") or 0)

    @property
    def is_on(self) -> bool:
        return self._level != 0

    @property
    def brightness(self) -> int:
        return round(self._level * 255 / 100)

    def _level_command(self, turn_on: bool) -> tuple[str, str]:
        """levelCmd ON/OFF when advertised by metadata, else level values."""
        enum_values = self._device.metadata.get("levelCmd", {}).get("enum_values", [])
        wanted = "ON" if turn_on else "OFF"
        if wanted in enum_values:
            return "levelCmd", wanted
        return "level", "100" if turn_on else "0"

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            await self._hub.async_send_command(self._device, "level", level)
            return
        await self._hub.async_send_command(
            self._device, *self._level_command(turn_on=True)
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self._hub.async_send_command(
            self._device, *self._level_command(turn_on=False)
        )
