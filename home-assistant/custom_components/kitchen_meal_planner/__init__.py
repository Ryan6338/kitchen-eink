"""Kitchen Meal Planner custom integration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any, Mapping

import voluptuous as vol
from aiomealie import MutateShoppingItem

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_CACHE_MINUTES,
    DEFAULT_MEAL_COUNT,
    DEFAULT_MINIMUM_QUICK,
    CATALOG_SENSOR_ENTITY_ID,
    DOMAIN,
    FUTURE_SENSOR_ENTITY_ID,
    SENSOR_ENTITY_ID,
    SERVICE_CLEAR_SHOPPING_LIST,
    SERVICE_GENERATE_FUTURE_PLAN,
    SERVICE_GENERATE_PLAN,
    SERVICE_LOAD_CATALOG,
    SERVICE_POPULATE_SHOPPING_LIST,
    SERVICE_REPLACE_MEAL,
    SERVICE_SET_MEAL,
    SERVICE_SYNC_PLAN,
    STATUS_ENTITY_ID,
)
from .planner import (
    CUSTOM_PREFIX,
    PlanResult,
    PlanningError,
    Recipe,
    SelectedMeal,
    generate_future_plan,
    generate_plan,
    normalize_label,
    recipe_from_mapping,
    replace_meal,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

ATTR_MEAL_COUNT = "meal_count"
ATTR_MINIMUM_QUICK = "minimum_quick_and_easy"
ATTR_SLOT = "slot"
ATTR_DRY_RUN = "dry_run"
ATTR_WEEKS = "weeks"
ATTR_VALUE = "value"
ATTR_LOCK = "lock"
ATTR_SHOPPING_LIST_ENTITY = "shopping_list_entity"

CARD_URL = "/kitchen_meal_planner/meal-picker-card.js"
CARD_PATH = Path(__file__).with_name("meal-picker-card.js")

GENERATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_MEAL_COUNT, default=DEFAULT_MEAL_COUNT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=7)
        ),
        vol.Optional(ATTR_MINIMUM_QUICK, default=DEFAULT_MINIMUM_QUICK): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=7)
        ),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

FUTURE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_WEEKS, default=4): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=8)
        ),
        vol.Optional(ATTR_MEAL_COUNT, default=DEFAULT_MEAL_COUNT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=7)
        ),
        vol.Optional(ATTR_MINIMUM_QUICK, default=DEFAULT_MINIMUM_QUICK): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=7)
        ),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

REPLACE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
        vol.Optional(ATTR_MINIMUM_QUICK, default=DEFAULT_MINIMUM_QUICK): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=7)
        ),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

LOAD_CATALOG_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
)

SET_MEAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
        vol.Required(ATTR_VALUE): vol.All(cv.string, vol.Length(min=1, max=255)),
        vol.Optional(ATTR_LOCK, default=False): cv.boolean,
    }
)

POPULATE_SHOPPING_LIST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SHOPPING_LIST_ENTITY): cv.entity_id,
    }
)

CLEAR_SHOPPING_LIST_SCHEMA = POPULATE_SHOPPING_LIST_SCHEMA


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Unsupported Mealie response type: {type(value).__name__}")


class RecipeRepository:
    """Retrieve and briefly cache complete recipes from Home Assistant's Mealie client."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._catalog: tuple[dict[str, str], ...] = ()
        self._catalog_expires_at = 0.0
        self._recipes: tuple[Recipe, ...] = ()
        self._expires_at = 0.0
        self._recipe_details: dict[str, tuple[Recipe, float]] = {}
        self._catalog_lock = asyncio.Lock()
        self._lock = asyncio.Lock()

    @property
    def client(self) -> Any:
        """Return the Mealie client associated with this cache."""

        return self._client

    async def async_get_catalog(self) -> tuple[dict[str, str], ...]:
        """Return the compact name/slug catalogue used by the dashboard picker."""

        if self._catalog and time.monotonic() < self._catalog_expires_at:
            return self._catalog

        async with self._catalog_lock:
            if self._catalog and time.monotonic() < self._catalog_expires_at:
                return self._catalog

            page = await self._client.get_recipes(per_page=1000)
            summaries = getattr(page, "items", None)
            if summaries is None and isinstance(page, Mapping):
                summaries = page.get("items", [])

            catalog: list[dict[str, str]] = []
            for summary in summaries or []:
                data = _as_mapping(summary)
                slug = str(data.get("slug") or data.get("id") or "").strip()
                name = str(data.get("name") or slug).strip()
                if slug and name:
                    catalog.append({"name": name, "slug": slug})
            catalog.sort(key=lambda item: item["name"].casefold())
            self._catalog = tuple(catalog)
            self._catalog_expires_at = (
                time.monotonic() + DEFAULT_CACHE_MINUTES * 60
            )
            return self._catalog

    async def async_get_recipes(self) -> tuple[Recipe, ...]:
        if self._recipes and time.monotonic() < self._expires_at:
            return self._recipes

        async with self._lock:
            if self._recipes and time.monotonic() < self._expires_at:
                return self._recipes

            catalog = await self.async_get_catalog()

            semaphore = asyncio.Semaphore(8)

            async def async_fetch(item: Mapping[str, str]) -> Recipe | None:
                slug = item["slug"]
                async with semaphore:
                    try:
                        detailed = await self._client.get_recipe(slug)
                    except Exception:  # noqa: BLE001 - skip one malformed/unavailable recipe
                        _LOGGER.warning("Unable to fetch Mealie recipe %s", slug, exc_info=True)
                        return None
                return recipe_from_mapping(_as_mapping(detailed))

            detailed = await asyncio.gather(
                *(async_fetch(item) for item in catalog)
            )
            self._recipes = tuple(recipe for recipe in detailed if recipe is not None)
            self._expires_at = time.monotonic() + DEFAULT_CACHE_MINUTES * 60
            self._recipe_details.update(
                {
                    recipe.slug: (recipe, self._expires_at)
                    for recipe in self._recipes
                }
            )
            return self._recipes

    async def async_get_recipe(self, slug: str) -> Recipe:
        """Return one detailed recipe without loading the entire catalogue."""

        cached = self._recipe_details.get(slug)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        detailed = await self._client.get_recipe(slug)
        recipe = recipe_from_mapping(_as_mapping(detailed))
        self._recipe_details[slug] = (
            recipe,
            time.monotonic() + DEFAULT_CACHE_MINUTES * 60,
        )
        return recipe


def _get_client(hass: HomeAssistant, entry_id: str) -> Any:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != "mealie":
        raise ServiceValidationError(f"Mealie config entry {entry_id!r} was not found")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("The selected Mealie config entry is not loaded")
    runtime_data = getattr(entry, "runtime_data", None)
    client = getattr(runtime_data, "client", None)
    if client is None:
        raise ServiceValidationError("The selected Mealie client is unavailable")
    return client


def _repository(hass: HomeAssistant, entry_id: str) -> RecipeRepository:
    repositories: dict[str, RecipeRepository] = hass.data.setdefault(DOMAIN, {})
    client = _get_client(hass, entry_id)
    repository = repositories.get(entry_id)
    if repository is None or repository.client is not client:
        repository = repositories[entry_id] = RecipeRepository(client)
    return repository


def _shopping_list_id(
    hass: HomeAssistant, entry_id: str, entity_id: str
) -> tuple[Any, str]:
    """Resolve a Mealie to-do entity to its runtime coordinator and list ID."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != "mealie" or entry.runtime_data is None:
        raise ServiceValidationError("The selected Mealie instance is unavailable")
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.platform != "mealie"
        or registry_entry.config_entry_id != entry_id
    ):
        raise ServiceValidationError(
            f"Shopping list entity {entity_id!r} does not belong to the selected Mealie instance"
        )
    coordinator = entry.runtime_data.shoppinglist_coordinator
    for list_id in coordinator.data:
        if registry_entry.unique_id == f"{entry.unique_id}_{list_id}":
            return coordinator, list_id
    raise ServiceValidationError(f"Mealie shopping list {entity_id!r} was not found")


def _csv_state(hass: HomeAssistant, entity_id: str) -> tuple[str, ...]:
    state = hass.states.get(entity_id)
    if state is None or state.state in {"", "unknown", "unavailable"}:
        return ()
    return tuple(
        term
        for item in state.state.split(",")
        if (term := normalize_label(item))
    )


def _recent_slugs(hass: HomeAssistant, cutoff_date: str) -> set[str]:
    state = hass.states.get("input_text.kitchen_meal_history")
    if state is None:
        return set()
    try:
        history = json.loads(state.state)
    except (TypeError, ValueError):
        return set()
    return {
        str(item.get("slug"))
        for item in history
        if isinstance(item, Mapping)
        and item.get("slug")
        and str(item.get("date", "")) >= cutoff_date
    }


def _special_terms(hass: HomeAssistant) -> tuple[str, ...]:
    state = hass.states.get("sensor.woolworths_specials")
    values = state.attributes.get("ingredient_terms") if state else None
    if not values:
        return _csv_state(hass, "input_text.kitchen_woolworths_specials")
    return tuple(
        term for item in values if (term := normalize_label(item))
    )


def _preferences(hass: HomeAssistant) -> dict[str, Any]:
    cutoff = dt_util.now().date() - timedelta(days=7)
    specials_state = hass.states.get(
        "input_boolean.kitchen_use_woolworths_specials"
    )
    return {
        "cutoff": cutoff,
        "recent_slugs": _recent_slugs(hass, cutoff.isoformat()),
        "seasonal_terms": _csv_state(
            hass, "input_text.kitchen_seasonal_ingredients"
        ),
        "use_up_terms": _csv_state(hass, "input_text.kitchen_use_up_ingredients"),
        "special_terms": _special_terms(hass),
        "use_specials": specials_state is not None and specials_state.state == "on",
    }


async def _async_set_text(hass: HomeAssistant, entity_id: str, value: str) -> None:
    if hass.states.get(entity_id) is None:
        return
    await hass.services.async_call(
        "input_text",
        "set_value",
        {"entity_id": entity_id, "value": value[:255]},
        blocking=True,
    )


def _score_dict(meal: SelectedMeal) -> dict[str, Any]:
    score = meal.score
    return {
        "total": round(score.total, 2),
        "seasonal": list(score.seasonal_matches),
        "use_up": list(score.use_up_matches),
        "shared": list(score.shared_ingredients),
        "specials": list(score.special_matches),
        "repeated_styles": list(score.repeated_styles),
        "quick_bonus": score.quick_bonus,
    }


def _meal_dict(hass: HomeAssistant, slot: int, meal: SelectedMeal) -> dict[str, Any]:
    day_state = hass.states.get(f"input_select.kitchen_meal_day_{slot}")
    return {
        "slot": slot,
        "day": day_state.state if day_state else "Unassigned",
        "slug": meal.recipe.slug,
        "name": meal.recipe.name,
        "custom": meal.recipe.slug.startswith(CUSTOM_PREFIX),
        "quick_and_easy": meal.recipe.is_quick_and_easy,
        "score": _score_dict(meal),
    }


def _active_meal_count(hass: HomeAssistant) -> int:
    """Read the configured active meal count safely."""

    state = hass.states.get("input_number.kitchen_meals_this_week")
    try:
        configured = int(float(state.state)) if state else DEFAULT_MEAL_COUNT
    except (TypeError, ValueError):
        configured = DEFAULT_MEAL_COUNT
    return min(7, max(1, configured))


def _slot_locked(hass: HomeAssistant, slot: int) -> bool:
    return hass.states.is_state(f"input_boolean.kitchen_meal_lock_{slot}", "on")


async def _async_publish_plan(
    hass: HomeAssistant,
    meals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> None:
    for meal in meals:
        slot = meal["slot"]
        await _async_set_text(
            hass, f"input_text.kitchen_meal_{slot}", str(meal["name"])
        )
        await _async_set_text(
            hass, f"input_text.kitchen_meal_{slot}_slug", str(meal["slug"])
        )

    hass.states.async_set(
        SENSOR_ENTITY_ID,
        f"{len(meals)} meals",
        {
            "friendly_name": "Kitchen meal plan generator",
            "icon": "mdi:silverware-fork-knife",
            "plan": meals,
            "diagnostics": diagnostics,
            "generated_at": dt_util.now().isoformat(),
        },
    )

    compatibility_plan = [
        {
            "slot": meal["slot"],
            "day": meal["day"],
            "slug": meal["slug"],
            "name": meal["name"],
        }
        for meal in meals
    ]
    serialized = json.dumps(compatibility_plan, separators=(",", ":"))
    if len(serialized) <= 255:
        await _async_set_text(hass, "input_text.kitchen_meal_plan", serialized)


def _publish_catalog(
    hass: HomeAssistant, catalog: tuple[dict[str, str], ...]
) -> None:
    hass.states.async_set(
        CATALOG_SENSOR_ENTITY_ID,
        f"{len(catalog)} recipes",
        {
            "friendly_name": "Kitchen meal recipe catalog",
            "icon": "mdi:book-open-variant",
            "recipes": list(catalog),
            "refreshed_at": dt_util.now().isoformat(),
        },
    )


async def _async_notify_failure(hass: HomeAssistant, message: str) -> None:
    await _async_set_text(hass, STATUS_ENTITY_ID, f"FAIL · {message}")
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": "Meal plan could not be generated", "message": message},
        blocking=True,
    )


def _diagnostics(result: PlanResult) -> dict[str, Any]:
    return {
        "eligible_recipes": result.eligible_count,
        "quick_and_easy_recipes": result.quick_eligible_count,
        "excluded_complex": result.excluded_complex_count,
        "excluded_recent": result.excluded_recent_count,
        "avoided_previous": result.avoided_previous_count,
    }


def _response(call: ServiceCall, data: dict[str, Any]) -> ServiceResponse:
    return data if call.return_response else None


def _next_monday() -> date:
    today = dt_util.now().date()
    return today + timedelta(days=7 - today.weekday())


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up services for the YAML-configured integration."""

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v=6")

    async def async_load_catalog(call: ServiceCall) -> ServiceResponse:
        try:
            catalog = await _repository(
                hass, call.data[ATTR_CONFIG_ENTRY_ID]
            ).async_get_catalog()
        except Exception as error:
            raise HomeAssistantError(f"Mealie catalogue load failed: {error}") from error
        _publish_catalog(hass, catalog)
        return _response(call, {"success": True, "recipe_count": len(catalog)})

    async def async_set_meal(call: ServiceCall) -> ServiceResponse:
        slot = call.data[ATTR_SLOT]
        value = call.data[ATTR_VALUE].strip()
        try:
            catalog = await _repository(
                hass, call.data[ATTR_CONFIG_ENTRY_ID]
            ).async_get_catalog()
        except Exception as error:
            raise HomeAssistantError(f"Mealie catalogue load failed: {error}") from error
        _publish_catalog(hass, catalog)

        normalized = value.casefold()
        match = next(
            (
                item
                for item in catalog
                if item["name"].casefold() == normalized
                or item["slug"].casefold() == normalized
            ),
            None,
        )
        if match:
            name = match["name"]
            slug = match["slug"]
            custom = False
        else:
            name = value
            digest = hashlib.sha256(value.casefold().encode()).hexdigest()[:16]
            slug = f"{CUSTOM_PREFIX}{digest}"
            custom = True

        await _async_set_text(hass, f"input_text.kitchen_meal_{slot}", name)
        await _async_set_text(hass, f"input_text.kitchen_meal_{slot}_slug", slug)
        if call.data[ATTR_LOCK]:
            await hass.services.async_call(
                "input_boolean",
                "turn_on",
                {"entity_id": f"input_boolean.kitchen_meal_lock_{slot}"},
                blocking=True,
            )
        await hass.services.async_call(
            DOMAIN, SERVICE_SYNC_PLAN, {}, blocking=True
        )
        return _response(
            call,
            {
                "success": True,
                "slot": slot,
                "name": name,
                "slug": slug,
                "custom": custom,
                "locked": call.data[ATTR_LOCK],
            },
        )

    async def async_populate_shopping_list(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[ATTR_SHOPPING_LIST_ENTITY]
        coordinator, list_id = _shopping_list_id(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], entity_id
        )
        current_plan = hass.states.get(SENSOR_ENTITY_ID)
        plan = current_plan.attributes.get("plan", []) if current_plan else []
        if not plan:
            raise ServiceValidationError("The current meal plan is empty")

        repository = _repository(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        planned_slugs = {
            str(meal.get("slug"))
            for meal in plan
            if isinstance(meal, Mapping)
            and meal.get("slug")
            and not str(meal.get("slug")).startswith(CUSTOM_PREFIX)
        }
        detailed_recipes = await asyncio.gather(
            *(repository.async_get_recipe(slug) for slug in planned_slugs)
        )
        recipes_by_slug = {recipe.slug: recipe for recipe in detailed_recipes}
        pantry_terms = _csv_state(hass, "input_text.kitchen_pantry")
        candidate_lines: list[str] = []
        seen: set[str] = set()
        for meal in plan:
            if not isinstance(meal, Mapping):
                continue
            slug = str(meal.get("slug") or "")
            name = str(meal.get("name") or "Custom meal")
            if slug.startswith(CUSTOM_PREFIX):
                ingredient_lines = (f"Ingredients for {name}",)
            else:
                recipe = recipes_by_slug.get(slug)
                if recipe is None:
                    raise ServiceValidationError(
                        f"Recipe {name!r} is no longer available in Mealie"
                    )
                ingredient_lines = recipe.ingredient_lines
            for line in ingredient_lines:
                normalized = normalize_label(line)
                if not normalized or any(term in normalized for term in pantry_terms):
                    continue
                key = normalized
                if key not in seen:
                    seen.add(key)
                    candidate_lines.append(line)

        if not candidate_lines:
            raise ServiceValidationError(
                "No grocery items were found in the current meal plan"
            )

        existing_items = coordinator.data[list_id].items
        existing_keys = {
            normalize_label(
                getattr(item, "display", None) or getattr(item, "note", None)
            )
            for item in existing_items
        }
        lines = [
            line
            for line in candidate_lines
            if normalize_label(line) not in existing_keys
        ]
        if not lines:
            message = f"Shopping list already up to date ({len(candidate_lines)} items)"
            await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
            return _response(
                call,
                {
                    "success": True,
                    "item_count": 0,
                    "existing_count": len(candidate_lines),
                    "shopping_list_entity": entity_id,
                    "message": message,
                },
            )

        positions = [
            item.position
            for item in existing_items
            if getattr(item, "position", None) is not None
        ]
        start_position = max(positions, default=-1) + 1
        semaphore = asyncio.Semaphore(2)

        async def async_add_line(index: int, line: str) -> None:
            item = MutateShoppingItem(note=line, quantity=0.0)
            item.list_id = list_id
            item.position = start_position + index
            async with semaphore:
                await coordinator.client.add_shopping_item(item)

        pending = list(enumerate(lines))
        errors_by_index: dict[int, Exception] = {}
        for attempt in range(3):
            results = await asyncio.gather(
                *(async_add_line(index, line) for index, line in pending),
                return_exceptions=True,
            )
            errors_by_index.update(
                {
                    index: result
                    for (index, _line), result in zip(pending, results, strict=True)
                    if isinstance(result, Exception)
                }
            )
            await coordinator.async_refresh()
            refreshed_keys = {
                normalize_label(
                    getattr(item, "display", None) or getattr(item, "note", None)
                )
                for item in coordinator.data[list_id].items
            }
            pending = [
                (index, line)
                for index, line in pending
                if normalize_label(line) not in refreshed_keys
            ]
            if not pending:
                break
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))

        if pending:
            added_count = len(lines) - len(pending)
            message = (
                f"FAILED · Added {added_count} of {len(lines)} items; "
                f"{len(pending)} can be retried"
            )
            await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
            first_index = pending[0][0]
            first_error = errors_by_index.get(first_index)
            raise HomeAssistantError(
                f"Could not add {len(pending)} of {len(lines)} shopping-list items: "
                f"{first_error or 'Mealie did not return the new items'}"
            ) from first_error
        message = f"Added {len(lines)} items to {entity_id}"
        await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
        _LOGGER.info(
            "Added %d grocery items to %s (%d already present)",
            len(lines),
            entity_id,
            len(candidate_lines) - len(lines),
        )
        return _response(
            call,
            {
                "success": True,
                "item_count": len(lines),
                "existing_count": len(candidate_lines) - len(lines),
                "shopping_list_entity": entity_id,
                "message": message,
            },
        )

    async def async_clear_shopping_list(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[ATTR_SHOPPING_LIST_ENTITY]
        coordinator, list_id = _shopping_list_id(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], entity_id
        )
        existing_items = tuple(coordinator.data[list_id].items)
        item_ids = {
            str(item.item_id)
            for item in existing_items
            if getattr(item, "item_id", None)
        }
        if len(item_ids) != len(existing_items):
            raise HomeAssistantError(
                "Mealie returned a shopping-list item without an ID; nothing was cleared"
            )
        if not item_ids:
            message = f"Shopping list already empty ({entity_id})"
            await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
            return _response(
                call,
                {
                    "success": True,
                    "item_count": 0,
                    "shopping_list_entity": entity_id,
                    "message": message,
                },
            )

        semaphore = asyncio.Semaphore(2)

        async def async_delete_item(item_id: str) -> None:
            async with semaphore:
                await coordinator.client.delete_shopping_item(item_id)

        pending = set(item_ids)
        errors_by_id: dict[str, Exception] = {}
        for attempt in range(3):
            deleting = tuple(pending)
            results = await asyncio.gather(
                *(async_delete_item(item_id) for item_id in deleting),
                return_exceptions=True,
            )
            errors_by_id.update(
                {
                    item_id: result
                    for item_id, result in zip(deleting, results, strict=True)
                    if isinstance(result, Exception)
                }
            )
            await coordinator.async_refresh()
            remaining_ids = {
                str(item.item_id)
                for item in coordinator.data[list_id].items
                if getattr(item, "item_id", None)
            }
            pending.intersection_update(remaining_ids)
            if not pending:
                break
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))

        if pending:
            removed_count = len(item_ids) - len(pending)
            message = (
                f"FAILED · Cleared {removed_count} of {len(item_ids)} items; "
                f"{len(pending)} can be retried"
            )
            await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
            first_id = next(iter(pending))
            first_error = errors_by_id.get(first_id)
            raise HomeAssistantError(
                f"Could not clear {len(pending)} of {len(item_ids)} shopping-list items: "
                f"{first_error or 'Mealie still returned the deleted items'}"
            ) from first_error

        message = f"Cleared {len(item_ids)} items from {entity_id}"
        await _async_set_text(hass, "input_text.kitchen_shopping_list", message)
        _LOGGER.info("Cleared %d grocery items from %s", len(item_ids), entity_id)
        return _response(
            call,
            {
                "success": True,
                "item_count": len(item_ids),
                "shopping_list_entity": entity_id,
                "message": message,
            },
        )

    async def async_generate(call: ServiceCall) -> ServiceResponse:
        dry_run = call.data[ATTR_DRY_RUN]
        if not dry_run:
            await _async_set_text(
                hass, STATUS_ENTITY_ID, "Running balanced meal-plan checks..."
            )
        try:
            recipes = await _repository(
                hass, call.data[ATTR_CONFIG_ENTRY_ID]
            ).async_get_recipes()
            by_slug = {recipe.slug: recipe for recipe in recipes}
            locked: dict[int, Recipe] = {}
            avoid_slugs: set[str] = set()
            for slot in range(1, call.data[ATTR_MEAL_COUNT] + 1):
                slug_state = hass.states.get(
                    f"input_text.kitchen_meal_{slot}_slug"
                )
                slug = slug_state.state if slug_state else ""
                if not _slot_locked(hass, slot):
                    if slug in by_slug:
                        avoid_slugs.add(slug)
                    continue
                if slug.startswith(CUSTOM_PREFIX):
                    name_state = hass.states.get(f"input_text.kitchen_meal_{slot}")
                    name = name_state.state.strip() if name_state else ""
                    if not name:
                        raise PlanningError(
                            f"Locked custom meal {slot} has no name"
                        )
                    locked[slot] = Recipe(slug=slug, name=name)
                    continue
                if slug not in by_slug:
                    raise PlanningError(
                        f"Locked meal {slot} does not identify an available Mealie recipe"
                    )
                locked[slot] = by_slug[slug]
            result = generate_plan(
                recipes,
                count=call.data[ATTR_MEAL_COUNT],
                minimum_quick=call.data[ATTR_MINIMUM_QUICK],
                locked=locked,
                avoid_slugs=avoid_slugs,
                **_preferences(hass),
            )
        except PlanningError as error:
            if not dry_run:
                await _async_notify_failure(hass, str(error))
            return _response(call, {"success": False, "error": str(error)})
        except Exception as error:
            if not dry_run:
                await _async_set_text(
                    hass, STATUS_ENTITY_ID, "FAIL · Mealie request failed"
                )
            raise HomeAssistantError(f"Meal-plan generation failed: {error}") from error

        meals = []
        for slot, selected_meal in enumerate(result.meals, start=1):
            meal = _meal_dict(hass, slot, selected_meal)
            meal["locked"] = slot in locked
            meals.append(meal)
        diagnostics = _diagnostics(result)
        diagnostics["locked_meals"] = len(locked)
        if not dry_run:
            await _async_publish_plan(hass, meals, diagnostics)
            await _async_set_text(
                hass,
                STATUS_ENTITY_ID,
                f"PASS · {len(meals)} unique · "
                f"{sum(meal['quick_and_easy'] for meal in meals)} Quick and Easy · "
                f"{len(locked)} locked preserved · 0 complex · "
                "0 cooked in last 7 days",
            )
        return _response(
            call, {"success": True, "meals": meals, "diagnostics": diagnostics}
        )

    async def async_replace(call: ServiceCall) -> ServiceResponse:
        slot = call.data[ATTR_SLOT]
        dry_run = call.data[ATTR_DRY_RUN]
        if _slot_locked(hass, slot):
            message = f"Meal {slot} is locked; unlock it before replacing it"
            if not dry_run:
                await _async_notify_failure(hass, message)
            return _response(call, {"success": False, "error": message})
        if not dry_run:
            await _async_set_text(hass, STATUS_ENTITY_ID, f"Replacing meal {slot}...")
        try:
            recipes = await _repository(
                hass, call.data[ATTR_CONFIG_ENTRY_ID]
            ).async_get_recipes()
            by_slug = {recipe.slug: recipe for recipe in recipes}
            selected = []
            active_count = _active_meal_count(hass)
            active_count = max(active_count, slot)
            for number in range(1, min(active_count, 7) + 1):
                if number == slot:
                    continue
                state = hass.states.get(f"input_text.kitchen_meal_{number}_slug")
                if state and state.state in by_slug:
                    selected.append(by_slug[state.state])
            replacement = replace_meal(
                recipes,
                selected=selected,
                minimum_quick=call.data[ATTR_MINIMUM_QUICK],
                **_preferences(hass),
            )
        except PlanningError as error:
            if not dry_run:
                await _async_notify_failure(hass, str(error))
            return _response(call, {"success": False, "error": str(error)})
        except Exception as error:
            if not dry_run:
                await _async_set_text(
                    hass, STATUS_ENTITY_ID, "FAIL · Mealie request failed"
                )
            raise HomeAssistantError(f"Meal replacement failed: {error}") from error

        meal = _meal_dict(hass, slot, replacement)
        if not dry_run:
            current_state = hass.states.get(SENSOR_ENTITY_ID)
            existing_by_slot = {
                int(item.get("slot", 0)): dict(item)
                for item in (
                    current_state.attributes.get("plan", [])
                    if current_state
                    else []
                )
                if isinstance(item, Mapping) and item.get("slot")
            }
            await _async_set_text(hass, f"input_text.kitchen_meal_{slot}", meal["name"])
            await _async_set_text(
                hass, f"input_text.kitchen_meal_{slot}_slug", meal["slug"]
            )
            current_meals: list[dict[str, Any]] = []
            for number in range(1, min(active_count, 7) + 1):
                if number == slot:
                    current_meals.append(meal)
                    continue
                slug_state = hass.states.get(f"input_text.kitchen_meal_{number}_slug")
                recipe = by_slug.get(slug_state.state if slug_state else "")
                if recipe is None:
                    continue
                existing = existing_by_slot.get(number, {})
                day_state = hass.states.get(
                    f"input_select.kitchen_meal_day_{number}"
                )
                if existing.get("slug") == recipe.slug:
                    existing.update(
                        {
                            "slot": number,
                            "day": day_state.state if day_state else "Unassigned",
                            "slug": recipe.slug,
                            "name": recipe.name,
                            "quick_and_easy": recipe.is_quick_and_easy,
                        }
                    )
                    current_meals.append(existing)
                else:
                    current_meals.append(
                        {
                            "slot": number,
                            "day": day_state.state if day_state else "Unassigned",
                            "slug": recipe.slug,
                            "name": recipe.name,
                            "quick_and_easy": recipe.is_quick_and_easy,
                            "score": {},
                        }
                    )
            current_meals.sort(key=lambda item: item["slot"])
            await _async_publish_plan(
                hass,
                current_meals,
                {"replacement_slot": slot},
            )
            await _async_set_text(
                hass,
                STATUS_ENTITY_ID,
                f"PASS · meal {slot} replaced · plan constraints preserved",
            )
        return _response(call, {"success": True, "meal": meal})

    async def async_generate_future(call: ServiceCall) -> ServiceResponse:
        dry_run = call.data[ATTR_DRY_RUN]
        if not dry_run:
            await _async_set_text(hass, STATUS_ENTITY_ID, "Generating future meal plans...")
        try:
            recipes = await _repository(
                hass, call.data[ATTR_CONFIG_ENTRY_ID]
            ).async_get_recipes()
            result = generate_future_plan(
                recipes,
                weeks=call.data[ATTR_WEEKS],
                meals_per_week=call.data[ATTR_MEAL_COUNT],
                minimum_quick_per_week=call.data[ATTR_MINIMUM_QUICK],
                **_preferences(hass),
            )
        except PlanningError as error:
            if not dry_run:
                await _async_notify_failure(hass, str(error))
            return _response(call, {"success": False, "error": str(error)})
        except Exception as error:
            if not dry_run:
                await _async_set_text(
                    hass, STATUS_ENTITY_ID, "FAIL · future Mealie request failed"
                )
            raise HomeAssistantError(f"Future meal-plan generation failed: {error}") from error

        start = _next_monday()
        weeks: list[dict[str, Any]] = []
        for week_index, week_result in enumerate(result.weeks):
            meals = [
                _meal_dict(hass, slot, meal)
                for slot, meal in enumerate(week_result.meals, start=1)
            ]
            weeks.append(
                {
                    "week": week_index + 1,
                    "week_start": (start + timedelta(days=week_index * 7)).isoformat(),
                    "meals": meals,
                    "diagnostics": _diagnostics(week_result),
                }
            )
        if not dry_run:
            hass.states.async_set(
                FUTURE_SENSOR_ENTITY_ID,
                f"{len(weeks)} weeks",
                {
                    "friendly_name": "Kitchen future meal plan generator",
                    "icon": "mdi:calendar-month",
                    "weeks": weeks,
                    "generated_at": dt_util.now().isoformat(),
                },
            )
            await _async_set_text(
                hass,
                STATUS_ENTITY_ID,
                f"PASS · {len(weeks)} future weeks generated without duplicates",
            )
        return _response(
            call,
            {
                "success": True,
                "week_count": len(weeks),
                "meal_count": sum(len(week["meals"]) for week in weeks),
            },
        )

    async def async_sync_plan(call: ServiceCall) -> ServiceResponse:
        current_state = hass.states.get(SENSOR_ENTITY_ID)
        current_plan = current_state.attributes.get("plan", []) if current_state else []
        by_slot = {
            int(meal.get("slot", 0)): dict(meal)
            for meal in current_plan
            if isinstance(meal, Mapping) and meal.get("slot")
        }
        active_count = _active_meal_count(hass)
        meals: list[dict[str, Any]] = []
        for slot in range(1, min(active_count, 7) + 1):
            name_state = hass.states.get(f"input_text.kitchen_meal_{slot}")
            slug_state = hass.states.get(f"input_text.kitchen_meal_{slot}_slug")
            if name_state is None or slug_state is None or not slug_state.state:
                continue
            day_state = hass.states.get(f"input_select.kitchen_meal_day_{slot}")
            meal = by_slot.get(slot, {"slot": slot})
            if meal.get("slug") != slug_state.state:
                meal = {"slot": slot, "score": {}, "quick_and_easy": False}
            meal.update(
                {
                    "day": day_state.state if day_state else "Unassigned",
                    "slug": slug_state.state,
                    "name": name_state.state,
                    "locked": _slot_locked(hass, slot),
                    "custom": slug_state.state.startswith(CUSTOM_PREFIX),
                }
            )
            meals.append(meal)
        await _async_publish_plan(
            hass,
            meals,
            current_state.attributes.get("diagnostics", {}) if current_state else {},
        )
        return _response(call, {"success": True, "meal_count": len(meals)})

    hass.services.async_register(
        DOMAIN,
        SERVICE_LOAD_CATALOG,
        async_load_catalog,
        schema=LOAD_CATALOG_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MEAL,
        async_set_meal,
        schema=SET_MEAL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_POPULATE_SHOPPING_LIST,
        async_populate_shopping_list,
        schema=POPULATE_SHOPPING_LIST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SHOPPING_LIST,
        async_clear_shopping_list,
        schema=CLEAR_SHOPPING_LIST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_PLAN,
        async_generate,
        schema=GENERATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_PLAN,
        async_sync_plan,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPLACE_MEAL,
        async_replace,
        schema=REPLACE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_FUTURE_PLAN,
        async_generate_future,
        schema=FUTURE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    restored_plan = []
    for slot in range(1, 8):
        name_state = hass.states.get(f"input_text.kitchen_meal_{slot}")
        slug_state = hass.states.get(f"input_text.kitchen_meal_{slot}_slug")
        if (
            name_state is None
            or slug_state is None
            or slug_state.state in {"", "unknown", "unavailable"}
        ):
            continue
        day_state = hass.states.get(f"input_select.kitchen_meal_day_{slot}")
        restored_plan.append(
            {
                "slot": slot,
                "day": day_state.state if day_state else "Unassigned",
                "slug": slug_state.state,
                "name": name_state.state,
                "locked": _slot_locked(hass, slot),
                "custom": slug_state.state.startswith(CUSTOM_PREFIX),
            }
        )
    hass.states.async_set(
        SENSOR_ENTITY_ID,
        f"{len(restored_plan)} meals" if restored_plan else "Not generated",
        {
            "friendly_name": "Kitchen meal plan generator",
            "icon": "mdi:silverware-fork-knife",
            "plan": restored_plan,
            "diagnostics": {},
        },
    )
    hass.states.async_set(
        FUTURE_SENSOR_ENTITY_ID,
        "Not generated",
        {
            "friendly_name": "Kitchen future meal plan generator",
            "icon": "mdi:calendar-month",
            "weeks": [],
        },
    )
    hass.states.async_set(
        CATALOG_SENSOR_ENTITY_ID,
        "Not loaded",
        {
            "friendly_name": "Kitchen meal recipe catalog",
            "icon": "mdi:book-open-variant",
            "recipes": [],
        },
    )

    async def async_prime_catalog() -> None:
        entries = [
            entry
            for entry in hass.config_entries.async_entries("mealie")
            if entry.state is ConfigEntryState.LOADED
        ]
        if not entries:
            return
        try:
            catalog = await _repository(
                hass, entries[0].entry_id
            ).async_get_catalog()
        except Exception:  # noqa: BLE001 - the explicit load action can retry later
            _LOGGER.warning("Unable to prime the Mealie recipe catalogue", exc_info=True)
            return
        _publish_catalog(hass, catalog)

    hass.async_create_task(async_prime_catalog())
    return True
