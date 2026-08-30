"""Diagnostics support — an anonymized dump for issue reports
(TS-DiagnosticsDump). The user downloads it from the integration entry
and pastes it into a bug report, so it must never leak credentials or
household-identifying names."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import const

# Keys scrubbed wherever they appear (recursively, including inside the
# raw gateway /info payload): connection secrets, the alarm PIN, the
# gateway's own identifiers, and the home coordinates the gateway reports
# under /info geoloc (hardware truth 2026-08-14).
TO_REDACT = {
    const.CONF_HOST,
    const.CONF_MAC,
    const.CONF_EMAIL,
    const.CONF_PASSWORD,
    const.CONF_TYDOM_PASSWORD,
    const.CONF_PIN,
    "mac",
    "usb_mac",
    "unique_id",
    "latitude",
    "longitude",
}


def _device_dump(device) -> dict[str, Any]:
    """Protocol-level view of one endpoint — no user-set name or area
    label (those identify the household); ids, usage, and live state are
    what support needs."""
    return {
        "usage": device.usage,
        "device_id": device.device_id,
        "endpoint_id": device.endpoint_id,
        "in_area": device.area_name is not None,
        "data": device.data,
        "metadata_keys": sorted(device.metadata),
        "energy_dests": device.energy_dests,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    hub = entry.runtime_data
    return {
        "entry": {
            "config_mode": entry.data.get(const.CONF_CONFIG_MODE),
            "pin_configured": bool(entry.data.get(const.CONF_PIN)),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "gateway": async_redact_data(hub.gateway_info, TO_REDACT),
        "counts": {
            "devices": len(hub.devices),
            "areas": len(hub.areas),
            "scenarios": len(hub.scenarios),
        },
        "home_hvac": {
            "mode": hub.home_hvac_mode,
            "support": hub.home_hvac_support,
        },
        "devices": [_device_dump(d) for d in hub.devices.values()],
        "areas": [_device_dump(d) for d in hub.areas.values()],
    }
