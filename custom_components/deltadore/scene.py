"""Scene platform — gateway scenarios (TS-ExtendedPlatformsMapping)."""

from homeassistant.components.scene import Scene
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
    hub = entry.runtime_data
    async_add_entities(DeltaDoreScene(hub, scenario) for scenario in hub.scenarios)


class DeltaDoreScene(Scene):
    """One scene per scenario defined on the gateway."""

    def __init__(self, hub, scenario: dict) -> None:
        self._hub = hub
        self._scenario_id = scenario["id"]
        self._attr_name = scenario["name"]
        self._attr_unique_id = f"scene_{scenario['id']}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.mac)})

    async def async_activate(self, **kwargs) -> None:
        await self._hub.async_activate_scenario(self._scenario_id)
