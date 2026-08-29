"""Support for the Forecast.Solar sensor service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import Estimate

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import ForecastSolarConfigEntry
from .const import (
    CONF_DAILY_YIELD_SENSOR_DAYS,
    CONF_ENABLE_SELF_OPTIMIZATION,
    CONF_PV_ARRAYS,
    DOMAIN,
)
from .coordinator import ForecastSolarDataUpdateCoordinator
from .dwd_icon import _parse_pv_arrays


@dataclass(frozen=True)
class ForecastSolarSensorEntityDescription(SensorEntityDescription):
    """Describes a Forecast.Solar Sensor."""

    state: Callable[[Estimate], Any] | None = None


SENSORS: tuple[ForecastSolarSensorEntityDescription, ...] = (
    ForecastSolarSensorEntityDescription(
        key="energy_production_today",
        translation_key="energy_production_today",
        state=lambda estimate: estimate.energy_production_today,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
    ),
    ForecastSolarSensorEntityDescription(
        key="energy_production_today_remaining",
        translation_key="energy_production_today_remaining",
        state=lambda estimate: estimate.energy_production_today_remaining,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
    ),
    ForecastSolarSensorEntityDescription(
        key="energy_production_tomorrow",
        translation_key="energy_production_tomorrow",
        state=lambda estimate: estimate.energy_production_tomorrow,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_highest_peak_time_today",
        translation_key="power_highest_peak_time_today",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_highest_peak_time_tomorrow",
        translation_key="power_highest_peak_time_tomorrow",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_production_now",
        translation_key="power_production_now",
        device_class=SensorDeviceClass.POWER,
        state=lambda estimate: estimate.power_production_now,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_production_next_hour",
        translation_key="power_production_next_hour",
        state=lambda estimate: estimate.power_production_at_time(
            estimate.now() + timedelta(hours=1)
        ),
        device_class=SensorDeviceClass.POWER,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_production_next_12hours",
        translation_key="power_production_next_12hours",
        state=lambda estimate: estimate.power_production_at_time(
            estimate.now() + timedelta(hours=12)
        ),
        device_class=SensorDeviceClass.POWER,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    ForecastSolarSensorEntityDescription(
        key="power_production_next_24hours",
        translation_key="power_production_next_24hours",
        state=lambda estimate: estimate.power_production_at_time(
            estimate.now() + timedelta(hours=24)
        ),
        device_class=SensorDeviceClass.POWER,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    ForecastSolarSensorEntityDescription(
        key="energy_current_hour",
        translation_key="energy_current_hour",
        state=lambda estimate: estimate.energy_current_hour,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
    ),
    ForecastSolarSensorEntityDescription(
        key="energy_next_hour",
        translation_key="energy_next_hour",
        state=lambda estimate: estimate.sum_energy_production(1),
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
    ),
)
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ForecastSolarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Defer sensor setup to the shared sensor module."""
    coordinator = entry.runtime_data
    entry_name = entry.title or "Solar production forecast"

    entities: list[SensorEntity] = [
        ForecastSolarSensorEntity(
            entry_id=entry.entry_id,
            entry_name=entry_name,
            coordinator=coordinator,
            entity_description=entity_description,
        )
        for entity_description in SENSORS
    ]

    daily_sensor_days = int(entry.options.get(CONF_DAILY_YIELD_SENSOR_DAYS, 4))
    daily_sensor_days = max(2, min(16, daily_sensor_days))
    for offset_days in range(2, daily_sensor_days):
        # Use dedicated keys to avoid collisions with legacy disabled entities.
        sensor_key = f"energy_production_in_{offset_days}_days"
        translation_key = "energy_production_in_days_dynamic"
        translation_placeholders = {"days": str(offset_days)}

        entities.append(
            ForecastSolarEnergyInDaysSensor(
                entry_id=entry.entry_id,
                entry_name=entry_name,
                coordinator=coordinator,
                offset_days=offset_days,
                sensor_key=sensor_key,
                translation_key=translation_key,
                translation_placeholders=translation_placeholders,
            )
        )

    if bool(entry.options.get(CONF_ENABLE_SELF_OPTIMIZATION, False)):
        arrays = _parse_pv_arrays(entry.options.get(CONF_PV_ARRAYS, "[]"))
        entities.extend(
            [
                ForecastSolarAdaptiveOverviewSensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    metric_key="average_factor",
                    name="Adaptive factor",
                ),
                ForecastSolarAdaptiveOverviewSensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    metric_key="average_reference_1000",
                    name="Adaptive reference 1000 W/m2",
                    native_unit="W/kWp",
                ),
                ForecastSolarAdaptiveOverviewSensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    metric_key="samples_total",
                    name="Adaptive samples total",
                ),
            ]
        )

        for idx in range(len(arrays)):
            array_number = idx + 1
            entities.append(
                ForecastSolarAdaptiveArraySensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    array_index=idx,
                    metric_key="factor",
                    name=f"Adaptive factor array {array_number}",
                )
            )
            entities.append(
                ForecastSolarAdaptiveArraySensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    array_index=idx,
                    metric_key="reference_1000",
                    name=f"Adaptive reference 1000 W/m2 array {array_number}",
                    native_unit="W/kWp",
                )
            )
            entities.append(
                ForecastSolarAdaptiveArraySensor(
                    entry_id=entry.entry_id,
                    entry_name=entry_name,
                    coordinator=coordinator,
                    array_index=idx,
                    metric_key="samples",
                    name=f"Adaptive samples array {array_number}",
                )
            )

    async_add_entities(entities)


class ForecastSolarSensorEntity(
    CoordinatorEntity[ForecastSolarDataUpdateCoordinator], SensorEntity
):
    """Defines a Forecast.Solar sensor."""

    entity_description: ForecastSolarSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        entry_id: str,
        entry_name: str,
        coordinator: ForecastSolarDataUpdateCoordinator,
        entity_description: ForecastSolarSensorEntityDescription,
    ) -> None:
        """Initialize Forecast.Solar sensor."""
        super().__init__(coordinator=coordinator)
        self.entity_description = entity_description
        entry_slug = slugify(entry_name) or entry_id
        self.entity_id = f"{SENSOR_DOMAIN}.{entry_slug}_{entity_description.key}"
        self._attr_unique_id = f"{entry_id}_{entity_description.key}"

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry_id)},
            manufacturer="DWD (ICON)",
            model=coordinator.data.account_type.value,
            name=entry_name,
            configuration_url="https://www.dwd.de/",
        )

    @property
    def native_value(self) -> datetime | StateType:
        """Return the state of the sensor."""
        if self.entity_description.state is None:
            state: StateType | datetime = getattr(
                self.coordinator.data, self.entity_description.key
            )
        else:
            state = self.entity_description.state(self.coordinator.data)

        return state


class ForecastSolarAdaptiveOverviewSensor(
    CoordinatorEntity[ForecastSolarDataUpdateCoordinator], SensorEntity
):
    """Adaptive optimization overview sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        entry_id: str,
        entry_name: str,
        coordinator: ForecastSolarDataUpdateCoordinator,
        metric_key: str,
        name: str,
        native_unit: str | None = None,
    ) -> None:
        super().__init__(coordinator=coordinator)
        self._metric_key = metric_key
        self._attr_name = name
        self._attr_native_unit_of_measurement = native_unit

        entry_slug = slugify(entry_name) or entry_id
        suffix = slugify(name)
        self.entity_id = f"{SENSOR_DOMAIN}.{entry_slug}_{suffix}"
        self._attr_unique_id = f"{entry_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry_id)},
            manufacturer="DWD (ICON)",
            model=coordinator.data.account_type.value,
            name=entry_name,
            configuration_url="https://www.dwd.de/",
        )

    @property
    def native_value(self) -> StateType:
        snapshot = self.coordinator.get_adaptive_snapshot()
        value = snapshot.get(self._metric_key)
        return value


class ForecastSolarEnergyInDaysSensor(
    CoordinatorEntity[ForecastSolarDataUpdateCoordinator], SensorEntity
):
    """Dynamic sensor for energy production N days ahead."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        entry_id: str,
        entry_name: str,
        coordinator: ForecastSolarDataUpdateCoordinator,
        offset_days: int,
        sensor_key: str,
        translation_key: str | None,
        translation_placeholders: dict[str, str] | None,
    ) -> None:
        super().__init__(coordinator=coordinator)
        self._offset_days = offset_days
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
        self._attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_suggested_display_precision = 1

        if translation_key is not None:
            self._attr_translation_key = translation_key
        if translation_placeholders is not None:
            self._attr_translation_placeholders = translation_placeholders

        entry_slug = slugify(entry_name) or entry_id
        self.entity_id = f"{SENSOR_DOMAIN}.{entry_slug}_{sensor_key}"
        self._attr_unique_id = f"{entry_id}_{sensor_key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry_id)},
            manufacturer="DWD (ICON)",
            model=coordinator.data.account_type.value,
            name=entry_name,
            configuration_url="https://www.dwd.de/",
        )

    @property
    def native_value(self) -> StateType:
        return self.coordinator.data.energy_production_in_days(self._offset_days)


class ForecastSolarAdaptiveArraySensor(
    CoordinatorEntity[ForecastSolarDataUpdateCoordinator], SensorEntity
):
    """Adaptive optimization sensor for one PV array."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        entry_id: str,
        entry_name: str,
        coordinator: ForecastSolarDataUpdateCoordinator,
        array_index: int,
        metric_key: str,
        name: str,
        native_unit: str | None = None,
    ) -> None:
        super().__init__(coordinator=coordinator)
        self._array_index = array_index
        self._metric_key = metric_key
        self._attr_name = name
        self._attr_native_unit_of_measurement = native_unit

        entry_slug = slugify(entry_name) or entry_id
        suffix = slugify(name)
        self.entity_id = f"{SENSOR_DOMAIN}.{entry_slug}_{suffix}"
        self._attr_unique_id = f"{entry_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry_id)},
            manufacturer="DWD (ICON)",
            model=coordinator.data.account_type.value,
            name=entry_name,
            configuration_url="https://www.dwd.de/",
        )

    @property
    def native_value(self) -> StateType:
        return self.coordinator.get_adaptive_array_metric(
            self._array_index,
            self._metric_key,
        )




