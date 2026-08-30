"""Binary sensor platform — contacts, smoke detectors, and device
diagnostics (read-only)."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALARM_USAGES,
    DOOR_CONTACT_USAGES,
    SMOKE_USAGES,
    WINDOW_CONTACT_USAGES,
)
from .entity import DeltaDoreEntity
from .helpers import alarm_flag_active
from pytydom import has_pod_position

# Diagnostic signals (TS-MissingSensorsMapping): data key ->
# (device class, translation key).
_DIAGNOSTIC_KEYS = {
    "battDefect": (BinarySensorDeviceClass.BATTERY, "batt_defect"),
    "obstacleDefect": (BinarySensorDeviceClass.PROBLEM, "obstacle_defect"),
    "upDefect": (BinarySensorDeviceClass.PROBLEM, "up_defect"),
    "downDefect": (BinarySensorDeviceClass.PROBLEM, "down_defect"),
    "thermicDefect": (BinarySensorDeviceClass.PROBLEM, "thermic_defect"),
    "calibrationDefect": (BinarySensorDeviceClass.PROBLEM, "calibration_defect"),
    "intrusion": (BinarySensorDeviceClass.TAMPER, "intrusion"),
    "intrusionDetect": (BinarySensorDeviceClass.TAMPER, "intrusion"),
    "onFavPos": (None, "favorite_position"),
}

# Alarm central system faults (TS-ComfortSecurityMapping, Q75): data key
# -> (device class, translation key). Diagnostic category.
_ALARM_FAULT_KEYS = {
    "systOpenIssue": (BinarySensorDeviceClass.PROBLEM, "fault_open_issue"),
    # Self-protection (anti-tamper) faults: PROBLEM, not TAMPER, so a healthy
    # state reads "OK" instead of the tamper class's misleading "Non détectée"
    # / "not detected" — consistent with the rest of the fault family (Q79).
    "unitAutoProtect": (BinarySensorDeviceClass.PROBLEM, "fault_unit_tamper"),
    "systAutoProtect": (BinarySensorDeviceClass.PROBLEM, "fault_syst_tamper"),
    "unitBatteryDefect": (BinarySensorDeviceClass.BATTERY, "fault_unit_battery"),
    "systBatteryDefect": (BinarySensorDeviceClass.BATTERY, "fault_syst_battery"),
    "systSectorDefect": (BinarySensorDeviceClass.PROBLEM, "fault_sector"),
    "systSupervisionDefect": (BinarySensorDeviceClass.PROBLEM, "fault_supervision"),
    "systTechnicalDefect": (BinarySensorDeviceClass.PROBLEM, "fault_technical"),
    "networkDefect": (BinarySensorDeviceClass.PROBLEM, "fault_network"),
    "simDefect": (BinarySensorDeviceClass.PROBLEM, "fault_sim"),
    "remoteSurveyDefect": (BinarySensorDeviceClass.PROBLEM, "fault_remote_survey"),
    "videoLinkDefect": (BinarySensorDeviceClass.PROBLEM, "fault_video_link"),
}

# Alarm trigger types (Q76): the KIND of triggering, as SAFETY alerts
# (not diagnostic). data key -> translation key.
_ALARM_TRIGGER_KEYS = {
    "alarmSOS": "alarm_sos",
    "alarmTechnical": "alarm_technical",
}


def _primary_state_key(device) -> str | None:
    """The key an entity already consumes as its primary state.

    Plain contacts without openState read intrusionDetect as their
    OPENING state; it must not be duplicated as a diagnostic
    (TS-MissingSensorsMapping, primary-state exclusion).
    """
    is_contact = device.usage in WINDOW_CONTACT_USAGES or (
        device.usage in DOOR_CONTACT_USAGES and not has_pod_position(device)
    )
    if (
        is_contact
        and "openState" not in device.data
        and "openState" not in device.metadata
    ):
        return "intrusionDetect"
    return None


# Push integration: all I/O is serialized over the hub's single
# websocket session, so no per-entity update semaphore is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities = []
    for device in hub.devices.values():
        if device.usage in WINDOW_CONTACT_USAGES:
            entities.append(
                DeltaDoreContact(hub, device, BinarySensorDeviceClass.WINDOW)
            )
        elif device.usage in DOOR_CONTACT_USAGES and not has_pod_position(device):
            entities.append(DeltaDoreContact(hub, device, BinarySensorDeviceClass.DOOR))
        elif device.usage in SMOKE_USAGES:
            entities.append(DeltaDoreSmoke(hub, device))
        elif device.usage in ALARM_USAGES:
            # The Tyxal central's system faults, as diagnostics.
            for key in sorted(set(device.data) & set(_ALARM_FAULT_KEYS)):
                entities.append(DeltaDoreAlarmFault(hub, device, key))
            # Trigger types (SOS / technical) as safety alerts.
            for key in sorted(set(device.data) & set(_ALARM_TRIGGER_KEYS)):
                entities.append(DeltaDoreAlarmTrigger(hub, device, key))
        # Diagnostic signals, whatever the device's platform.
        primary = _primary_state_key(device)
        keys = set(device.data) | set(device.metadata)
        for key in sorted(keys & set(_DIAGNOSTIC_KEYS)):
            if key != primary:
                entities.append(DeltaDoreDiagnostic(hub, device, key))
    async_add_entities(entities)


class DeltaDoreContact(DeltaDoreEntity, BinarySensorEntity):
    """Door/window contact: open when openState is not "LOCKED"."""

    def __init__(self, hub, device, device_class) -> None:
        super().__init__(hub, device, "binary_sensor")
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        open_state = self._device.data.get("openState")
        if open_state is not None:
            return open_state != "LOCKED"
        return bool(self._device.data.get("intrusionDetect"))


class DeltaDoreSmoke(DeltaDoreEntity, BinarySensorEntity):
    """Smoke detector: smoke detected when techSmokeDefect is true."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(self, hub, device) -> None:
        super().__init__(hub, device, "binary_sensor")

    @property
    def is_on(self) -> bool:
        return bool(self._device.data.get("techSmokeDefect"))


class DeltaDoreDiagnostic(DeltaDoreEntity, BinarySensorEntity):
    """One diagnostic signal of a device (TS-MissingSensorsMapping)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, device, key: str) -> None:
        super().__init__(hub, device, f"binary_sensor_{key}")
        device_class, translation_key = _DIAGNOSTIC_KEYS[key]
        self._attr_device_class = device_class
        # Named through the translated signal name, not the device name.
        del self._attr_name
        self._attr_translation_key = translation_key
        self._key = key

    @property
    def is_on(self) -> bool | None:
        value = self._device.data.get(self._key)
        return None if value is None else bool(value)


class DeltaDoreAlarmTrigger(DeltaDoreEntity, BinarySensorEntity):
    """A KIND of alarm triggering (SOS / technical), as a SAFETY alert
    (TS-ComfortSecurityMapping, Q76)."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, hub, device, key: str) -> None:
        super().__init__(hub, device, f"binary_sensor_{key}")
        del self._attr_name
        self._attr_translation_key = _ALARM_TRIGGER_KEYS[key]
        self._key = key

    @property
    def is_on(self) -> bool | None:
        value = self._device.data.get(self._key)
        # Same coercion as the panel's TRIGGERED derivation (Q87), so the
        # two never disagree on a non-boolean flag encoding.
        return None if value is None else alarm_flag_active(value)


class DeltaDoreAlarmFault(DeltaDoreEntity, BinarySensorEntity):
    """One system fault of the alarm central (TS-ComfortSecurityMapping)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, device, key: str) -> None:
        super().__init__(hub, device, f"binary_sensor_{key}")
        device_class, translation_key = _ALARM_FAULT_KEYS[key]
        self._attr_device_class = device_class
        del self._attr_name
        self._attr_translation_key = translation_key
        self._key = key

    @property
    def is_on(self) -> bool | None:
        value = self._device.data.get(self._key)
        return None if value is None else bool(value)
