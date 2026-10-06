from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME

from .const import CONF_COOKIE, DOMAIN


class WoolworthsSpecialsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id("woolworths_specials")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, "Woolworths Specials"),
                data={
                    CONF_NAME: user_input.get(CONF_NAME, "Woolworths Specials"),
                    CONF_COOKIE: user_input[CONF_COOKIE],
                },
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Woolworths Specials"): str,
                vol.Required(CONF_COOKIE): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
