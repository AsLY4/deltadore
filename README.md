# Delta Dore for Home Assistant

A [Home Assistant](https://www.home-assistant.io/) integration for **Delta Dore
Tydom** home-automation gateways. It exposes the accessories paired to the
gateway — roller shutters, lights, sockets, heating, door/window contacts,
smoke detectors, a Tyxal alarm, and more — as native Home Assistant entities,
kept in sync in real time over a local push connection (a cloud connection is
also supported).

The gateway transport lives in a separate library,
[`pytydom`](https://github.com/AsLY4/pytydom), declared as the integration's
requirement.

## Installation (custom component)

Copy `custom_components/deltadore/` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant, then add the integration from
**Settings → Devices & services → Add integration → Delta Dore**.

Home Assistant installs the `pytydom` dependency automatically from PyPI.

## Supported functionality

Alarm control panel, binary sensors (contacts, smoke, diagnostics), climate
(thermostats and thermal zones), covers (shutters, awnings, garage, gate),
lights, locks, scenes, sensors (temperature, energy, per-zone alarm state),
switches (plugs, away mode), gateway firmware update, and weather. Setup
supports local, cloud, and manual modes, with DHCP discovery and an options
flow (gateway address, alarm PIN, alarm zones).

## Documentation & diagnostics

User documentation follows the Home Assistant integration page format. The
integration provides an anonymized **Download diagnostics** dump (credentials,
home coordinates and household names redacted) for issue reports.

## License

MIT — see [LICENSE](LICENSE).
