"""Lock platform — motorized doors (TS-ComfortSecurityMapping)."""

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOOR_CONTACT_USAGES
from .entity import DeltaDoreEntity
from pytydom import has_pod_position


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        DeltaDoreLock(hub, device)
        for device in hub.devices.values()
        if device.usage in DOOR_CONTACT_USAGES and has_pod_position(device)
    )


class DeltaDoreLock(DeltaDoreEntity, LockEntity):
    """Motorized door: locked when podPosition is LOCK; unlock closes it."""

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "lock")

    @property
    def is_locked(self) -> bool:
        return self._device.data.get("podPosition") == "LOCK"

    async def async_lock(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "podPosition", "LOCK")

    async def async_unlock(self, **kwargs) -> None:
        await self._hub.async_send_command(self._device, "podPosition", "CLOSE")
