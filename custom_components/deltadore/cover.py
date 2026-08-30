"""Cover platform — shutters, garage doors, gates (TS-CorePlatformsMapping,
TS-ExtendedPlatformsMapping)."""

from homeassistant.components.cover import (
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COVER_USAGES, GARAGE_USAGES, GATE_USAGES
from .entity import DeltaDoreEntity


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities = []
    for device in hub.devices.values():
        if device.usage in COVER_USAGES:
            entities.append(DeltaDoreCover(hub, device))
        elif device.usage in GARAGE_USAGES:
            entities.append(DeltaDoreGarage(hub, device))
        elif device.usage in GATE_USAGES:
            entities.append(DeltaDoreGate(hub, device))
    async_add_entities(entities)


class DeltaDoreCover(DeltaDoreEntity, CoverEntity):
    """Shutter with position (0 = closed, 100 = open), tilt when sloped."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "cover")
        if "slope" in device.data or "slope" in device.metadata:
            self._attr_supported_features |= CoverEntityFeature.SET_TILT_POSITION

    @property
    def current_cover_position(self) -> int | None:
        position = self._device.data.get("position")
        return None if position is None else int(position)

    @property
    def current_cover_tilt_position(self) -> int | None:
        slope = self._device.data.get("slope")
        return None if slope is None else int(slope)

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    async def async_open_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "positionCmd", "UP")

    async def async_close_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "positionCmd", "DOWN")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "positionCmd", "STOP")

    async def async_set_cover_position(self, **kwargs) -> None:
        await self._hub.async_send_command(
            self._device, "position", int(kwargs["position"])
        )

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        await self._hub.async_send_command(
            self._device, "slope", int(kwargs[ATTR_TILT_POSITION])
        )


class _LevelCmdCover(DeltaDoreEntity, CoverEntity):
    """Cover driven by levelCmd ON/OFF/STOP (garage doors and gates)."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "cover")

    async def async_open_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "levelCmd", "ON")

    async def async_close_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "levelCmd", "OFF")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "levelCmd", "STOP")


class DeltaDoreGarage(_LevelCmdCover):
    """Garage door: level 0 = closed."""

    _attr_device_class = CoverDeviceClass.GARAGE

    @property
    def is_closed(self) -> bool | None:
        level = self._device.data.get("level")
        return None if level is None else int(level) == 0


class DeltaDoreGate(_LevelCmdCover):
    """Gate: closed while openState is LOCKED."""

    _attr_device_class = CoverDeviceClass.GATE

    @property
    def is_closed(self) -> bool | None:
        open_state = self._device.data.get("openState")
        return None if open_state is None else open_state == "LOCKED"
