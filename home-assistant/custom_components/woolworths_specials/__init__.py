from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import CONF_COOKIE, DOMAIN
from .coordinator import WoolworthsSpecialsCoordinator

PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = WoolworthsSpecialsCoordinator(
        hass,
        cookie=entry.data[CONF_COOKIE],
    )
    await coordinator.async_config_entry_first_refresh()

    async def refresh_on_thursday(_now) -> None:
        if _now.weekday() == 3:  # Thursday in Home Assistant's local timezone.
            await coordinator.async_request_refresh()

    remove_listener = async_track_time_change(
        hass,
        refresh_on_thursday,
        hour=6,
        minute=0,
        second=0,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "remove_listener": remove_listener,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            data["remove_listener"]()
    return unloaded
