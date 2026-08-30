"""Alarm control panel platform (TS-ComfortSecurityMapping).

Full Tyxal model: triggered/pending/away/partial-by-zones/disarmed
state, alarmCmd (total) and zoneCmd (partial) commands with the stored
PIN.
"""

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALARM_USAGES
from .entity import DeltaDoreEntity
from .helpers import alarm_flag_active

# HA partial-arm mode -> (config zone-list key, resulting HA state).
_ZONE_MODES = (
    ("home", AlarmControlPanelState.ARMED_HOME),
    ("night", AlarmControlPanelState.ARMED_NIGHT),
    ("custom", AlarmControlPanelState.ARMED_CUSTOM_BYPASS),
)
_TRIGGER_FLAGS = ("alarmSOS", "alarmTechnical", "preAlarm")
# Documented alarmMode values that mean "not armed".
_DISARMED_MODES = (None, "", "OFF", "MAINTENANCE")


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        DeltaDoreAlarm(hub, device)
        for device in hub.devices.values()
        if device.usage in ALARM_USAGES
    )


class DeltaDoreAlarm(DeltaDoreEntity, AlarmControlPanelEntity):
    """Tyxal alarm: total and per-zone arming with the stored PIN."""

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        | AlarmControlPanelEntityFeature.TRIGGER
    )
    _attr_code_arm_required = False

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "alarm_control_panel")

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        data = self._device.data
        if data.get("alarmState") in ("ON", "QUIET") or any(
            alarm_flag_active(data.get(flag)) for flag in _TRIGGER_FLAGS
        ):
            return AlarmControlPanelState.TRIGGERED
        if data.get("alarmState") == "DELAYED":
            return AlarmControlPanelState.PENDING
        mode = data.get("alarmMode")
        if mode == "ON":
            return AlarmControlPanelState.ARMED_AWAY
        if mode == "ZONE":
            armed = {
                zone for zone in range(1, 9) if data.get(f"zone{zone}State") == "ON"
            }
            for config_mode, state in _ZONE_MODES:
                configured = set(self._hub.alarm_zones(config_mode))
                if configured and configured == armed:
                    return state
            return AlarmControlPanelState.ARMED_CUSTOM_BYPASS
        if mode in _DISARMED_MODES:
            return AlarmControlPanelState.DISARMED
        # A non-empty alarmMode outside the documented set (firmware
        # variation): never claim disarmed for an armed central — report
        # unknown so the state is not silently mis-shown as off (Q87).
        return None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._ensure_armable()
        await self._hub.async_send_alarm_command(self._device, "ON", self._pin())

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._hub.async_send_alarm_command(self._device, "OFF", self._pin())

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        self._ensure_armable()
        await self._hub.async_send_alarm_command(self._device, "PANIC", self._pin())

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._async_arm_zones("home")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._async_arm_zones("night")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self._async_arm_zones("custom")

    def _ensure_armable(self) -> None:
        """The central forbids arming in maintenance (hardware 2026-07-21)."""
        if self._device.data.get("alarmMode") == "MAINTENANCE":
            raise HomeAssistantError(
                "The alarm is in maintenance mode and cannot be armed"
            )

    async def _async_arm_zones(self, mode: str) -> None:
        self._ensure_armable()
        zones = self._hub.alarm_zones(mode)
        if not zones:
            raise HomeAssistantError(
                f"No alarm zones configured for the {mode} mode; declare them "
                "in the integration options first"
            )
        await self._hub.async_send_alarm_zone_command(
            self._device, "ON", self._pin(), zones
        )

    def _pin(self) -> str:
        pin = self._hub.alarm_pin
        if not pin:
            raise HomeAssistantError(
                "No alarm PIN configured for this gateway; reconfigure the "
                "integration with a PIN to control the alarm"
            )
        return pin
