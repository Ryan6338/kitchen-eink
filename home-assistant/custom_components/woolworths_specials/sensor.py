from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WoolworthsSpecialsCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: WoolworthsSpecialsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([WoolworthsSpecialsSensor(coordinator)])


class WoolworthsSpecialsSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Woolworths Specials"
    _attr_unique_id = "woolworths_specials"
    _attr_icon = "mdi:cart-percent"

    def __init__(self, coordinator: WoolworthsSpecialsCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("products", [])) if self.coordinator.data else 0

    @property
    def native_unit_of_measurement(self) -> str:
        return "products"

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.data or {}
