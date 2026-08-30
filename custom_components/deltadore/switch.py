"""Switch platform — plugs (TS-CorePlatformsMapping) and the absence
switch (TS-AwayModeMapping)."""

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SWITCH_USAGES
from .entity import DeltaDoreEntity


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities = [
        DeltaDoreSwitch(hub, device)
        for device in hub.devices.values()
        if device.usage in SWITCH_USAGES
    ]
    entities.append(DeltaDoreAbsenceSwitch(hub))
    async_add_entities(entities)


class DeltaDoreSwitch(DeltaDoreEntity, SwitchEntity):
    """Plug driven by plugCmd ("ON"/"OFF")."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "switch")

    @property
    def is_on(self) -> bool:
        return self._device.data.get("plugCmd") == "ON"

    async def async_turn_on(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "plugCmd", "ON")

    async def async_turn_off(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "plugCmd", "OFF")


class DeltaDoreAbsenceSwitch(SwitchEntity):
    """Global program suspension, on the gateway device
    (TS-AwayModeMapping): on = suspended (indefinitely or until the
    absence end date), off = the program runs. Never touches the
    per-moment suspensions."""

    _attr_has_entity_name = True
    _attr_translation_key = "absence"
    _attr_should_poll = False

    def __init__(self, hub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.mac}_absence"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.mac)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.add_info_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._hub.absence_to != 0

    async def async_turn_on(self, **kwargs) -> None:
        await self._hub.async_set_absence(-1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._hub.async_set_absence(0)
