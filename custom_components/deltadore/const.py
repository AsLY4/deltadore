"""Constants for the Delta Dore integration.

The module-level values below are contract seams: they carry the production
defaults and are overridden by the acceptance tests to target fake servers.
Always read them through the module (`const.X`), never bind them at import.
"""

DOMAIN = "deltadore"

# Delta Dore cloud (TS-DeltaDoreCloudAuth)
CLOUD_DISCOVERY_URL = (
    "https://deltadoreadb2ciot.b2clogin.com/deltadoreadb2ciot.onmicrosoft.com"
    "/v2.0/.well-known/openid-configuration?p=B2C_1_AccountProviderROPC_SignIn"
)
CLOUD_SITES_URL = "https://prod.iotdeltadore.com/sitesmanagement/api/v1/sites"
CLOUD_CLIENT_ID = "8782839f-3264-472a-ab87-4d4e23524da4"
CLOUD_SCOPE = (
    "openid profile offline_access"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/video_config"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/video_allowed"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/sites_management_allowed"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/sites_management_gateway_credentials"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/sites_management_camera_credentials"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/comptage_europe_collect_reader"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/comptage_europe_site_config_contributor"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/pilotage_allowed"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/consent_mgt_contributor"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/b2caccountprovider_manage_account"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/b2caccountprovider_allow_view_account"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/tydom_backend_allowed"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/websocket_remote_access"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/orkestrator_device"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/orkestrator_view"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/orkestrator_space"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/orkestrator_connector"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/orkestrator_endpoint"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/rule_management_allowed"
    " https://deltadoreadb2ciot.onmicrosoft.com/iotapi/collect_read_datas"
)

# Tydom gateway (TS-TydomGatewayHandshake, TS-GatewaySessionLifecycle)
MEDIATION_HOST = "mediation.tydom.com"
GATEWAY_PORT = 443
GATEWAY_TLS_VERIFY = True
REQUEST_TIMEOUT = 60
RECONNECT_INTERVAL = 10
# Real gateways drop a silent session after ~60 s (hardware, 2026-07-20).
KEEPALIVE_INTERVAL = 20
# Periodic /info re-read: absence and firmware state follow app-side
# changes (TS-AwayModeMapping).
INFO_REFRESH_INTERVAL = 300

# Config flow (TS-IntegrationSetupConfigFlow)
CONF_CONFIG_MODE = "config_mode"
CONF_HOST = "host"
CONF_MAC = "mac"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TYDOM_PASSWORD = "tydom_password"
CONF_PIN = "pin"
# Alarm zone lists per HA arm mode (config-options; consumed by the
# future alarm control panel). away/vacation = the whole-alarm total.
CONF_ZONES_HOME = "zones_home"
CONF_ZONES_NIGHT = "zones_night"
CONF_ZONES_CUSTOM = "zones_custom"

MODE_LOCAL = "local"
MODE_CLOUD = "cloud"
MODE_MANUAL = "manual"

# Usage → platform mapping (TS-CorePlatformsMapping)
COVER_USAGES = {"shutter", "klineShutter", "awning", "swingShutter"}
LIGHT_USAGES = {"light"}
SWITCH_USAGES = {"plug"}
WINDOW_CONTACT_USAGES = {
    "window",
    "windowFrench",
    "windowSliding",
    "klineWindowFrench",
    "klineWindowSliding",
}
DOOR_CONTACT_USAGES = {"belmDoor", "klineDoor"}
SMOKE_USAGES = {"sensorDFR"}
TEMPERATURE_USAGES = {"sensorThermo"}
# TS-ComfortSecurityMapping (Milestone 2)
CLIMATE_USAGES = {"boiler", "sh_hvac", "electric", "aeraulic"}
ALARM_USAGES = {"alarm"}
# TS-ExtendedPlatformsMapping (Milestone 3)
ENERGY_USAGES = {"conso"}
WEATHER_USAGES = {"weather"}
GARAGE_USAGES = {"garage_door"}
GATE_USAGES = {"gate"}
SUN_USAGES = {"sensorSun"}
SUPPORTED_USAGES = (
    COVER_USAGES
    | LIGHT_USAGES
    | SWITCH_USAGES
    | WINDOW_CONTACT_USAGES
    | DOOR_CONTACT_USAGES
    | SMOKE_USAGES
    | TEMPERATURE_USAGES
    | CLIMATE_USAGES
    | ALARM_USAGES
    | ENERGY_USAGES
    | WEATHER_USAGES
    | GARAGE_USAGES
    | GATE_USAGES
    | SUN_USAGES
)

UNIT_MAP = {"degC": "°C", "degF": "°F", "W/m2": "W/m²", "NA": None, "": None}

# Energy index polling (TS-ExtendedPlatformsMapping); seconds.
ENERGY_POLL_INTERVAL = 300
INSTANT_POWER_KEY = "energyInstantTotElecP"
PLUG_ENERGY_INDEX_KEY = "energyTotIndexWatt"
TEMPERATURE_DEST_PREFIX = "TEMP_"

# Sanity cap for reassembling a message split across websocket frames.
FRAME_BUFFER_LIMIT = 1_048_576
