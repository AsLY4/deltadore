"""Base entity for gateway devices (TS-CorePlatformsMapping)."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hub import GatewayHub
from pytydom import TydomDevice


class DeltaDoreEntity(Entity):
    """One primary entity per supported gateway device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: GatewayHub, device: TydomDevice, platform_key: str) -> None:
        self._hub = hub
        self._device = device
        # Instance-level so subclasses can delete it to let a
        # translation key provide the entity name instead.
        self._attr_name = None
        self._attr_unique_id = f"{device.key}_{platform_key}"
        info = DeviceInfo(
            identifiers={(DOMAIN, device.key)},
            name=device.name,
            manufacturer="Delta Dore",
            via_device=(DOMAIN, hub.mac),
        )
        # Group the device under its thermal zone when the gateway links
        # it to an area (TS-AreasZonesMapping); no-op otherwise.
        if getattr(device, "area_name", None):
            info["suggested_area"] = device.area_name
        self._attr_device_info = info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._device.add_listener(self.async_write_ha_state))
