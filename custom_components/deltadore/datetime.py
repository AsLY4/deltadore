"""Datetime platform — the absence end date (TS-AwayModeMapping)."""

from datetime import datetime, timezone

from homeassistant.components.datetime import DateTimeEntity
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
    async_add_entities([DeltaDoreAbsenceEnd(entry.runtime_data)])


class DeltaDoreAbsenceEnd(DateTimeEntity):
    """End of the global program suspension, on the gateway device.

    The wire value is the gateway's LOCAL wall-clock laid onto the
    epoch (pseudo-epoch); conversions go through the gateway clock
    from /info — never the HA timezone (TS-AwayModeMapping).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "absence_end"
    _attr_should_poll = False

    def __init__(self, hub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.mac}_absence_end"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.mac)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.add_info_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> datetime | None:
        to = self._hub.absence_to
        if to <= 0:
            return None  # program active, or suspended indefinitely
        pseudo = datetime.fromtimestamp(to, tz=timezone.utc)
        return pseudo - self._hub.gateway_utc_offset()

    async def async_set_value(self, value: datetime) -> None:
        local = value.astimezone(timezone.utc) + self._hub.gateway_utc_offset()
        await self._hub.async_set_absence(int(local.timestamp()))
