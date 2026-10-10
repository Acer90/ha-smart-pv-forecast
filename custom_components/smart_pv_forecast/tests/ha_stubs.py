"""Small HA API doubles; these tests do not replace a live HA smoke test."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    return result


def identity(function):
    return function


class ConfigEntry:
    @classmethod
    def __class_getitem__(cls, item):
        return cls


class DataUpdateCoordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, config_entry, **kwargs):
        self.hass = hass
        self.config_entry = config_entry
        self.data = None


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


def install():
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers", "homeassistant.util"):
        module(name)
    module("homeassistant.const", CONF_LATITUDE="latitude", CONF_LONGITUDE="longitude",
           CONF_NAME="name", Platform=Mock(SENSOR="sensor"))
    module("homeassistant.core", HomeAssistant=object, callback=identity)
    module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=Flow,
           ConfigFlowResult=dict, OptionsFlow=Flow)
    module("homeassistant.helpers.typing", ConfigType=dict)
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    module("homeassistant.helpers.storage", Store=Mock(return_value=Mock(async_remove=AsyncMock())))
    module("homeassistant.helpers.entity_registry", async_get=Mock(),
           async_entries_for_config_entry=Mock(return_value=[]))
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator,
           UpdateFailed=type("UpdateFailed", (Exception,), {}))
    from datetime import datetime, timezone
    module("homeassistant.util.dt", now=lambda: datetime.now(timezone.utc))
    module("homeassistant.components.frontend", async_panel_exists=Mock(return_value=False),
           async_remove_panel=Mock(), add_extra_js_url=Mock())
    module("homeassistant.components.lovelace")
    module("homeassistant.components.lovelace.resources",
           ResourceStorageCollection=type("ResourceStorageCollection", (), {}))
    module("homeassistant.components.panel_custom", async_register_panel=AsyncMock())
    module("homeassistant.components.http", StaticPathConfig=lambda *args: args)
    module("homeassistant.components.websocket_api",
           websocket_command=lambda schema: identity, require_admin=identity,
           async_response=identity, ActiveConnection=object, async_register_command=Mock())
    module("homeassistant.helpers.config_validation", latitude=float, longitude=float)
    module("homeassistant.helpers.selector",
           NumberSelector=lambda config: float,
           NumberSelectorConfig=lambda **kwargs: kwargs,
           NumberSelectorMode=Mock(BOX="box"),
           SelectSelector=lambda config: str,
           SelectSelectorConfig=lambda **kwargs: kwargs)
