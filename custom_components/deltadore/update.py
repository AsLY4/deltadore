"""Update platform — gateway firmware (TS-ExtendedPlatformsMapping)."""

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DeltaDoreGatewayUpdate(entry.runtime_data)])


class DeltaDoreGatewayUpdate(UpdateEntity):
    """Firmware status of the gateway itself, read from /info at sync.

    The API exposes no target version: when the gateway flags
    updateAvailable, latest_version returns the literal "update available"
    so Home Assistant signals it.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, hub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.mac}_update"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.mac)})

    @property
    def installed_version(self) -> str | None:
        return self._hub.gateway_info.get("mainVersionSW")

    @property
    def latest_version(self) -> str | None:
        if self._hub.gateway_info.get("updateAvailable"):
            return "update available"
        return self.installed_version

    async def async_install(self, version, backup: bool, **kwargs) -> None:
        await self._hub.async_trigger_gateway_update()
