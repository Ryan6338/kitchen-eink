from __future__ import annotations

import logging
import re
from typing import Any

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import BASE_URL, CATEGORY_URLS, SEARCH_URL

_LOGGER = logging.getLogger(__name__)
_DROP_WORDS = {
    "woolworths", "fresh", "new", "seasonal", "each", "pack", "packs",
    "bunch", "bag", "bags", "tray", "trays", "product", "approx", "approx.",
    "organic", "free", "range", "australian", "kg", "g", "gram", "grams",
    "ml", "l", "litre", "litres", "size", "large", "medium", "small",
}


def _terms(name: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", name.lower())
    return [word for word in words if word not in _DROP_WORDS and not word.isnumeric()]


def _products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    products = payload.get("Products") or payload.get("products") or []
    if isinstance(products, dict):
        products = products.get("items", [])
    return products if isinstance(products, list) else []


class WoolworthsSpecialsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, cookie: str) -> None:
        self.cookie = cookie.strip()
        super().__init__(
            hass,
            _LOGGER,
            name="Woolworths specials",
            # Refreshing is scheduled explicitly every Thursday in __init__.py.
            update_interval=None,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        websession = async_get_clientsession(self.hass)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "User-Agent": "Mozilla/5.0 Home Assistant Woolworths Specials",
            "Cookie": self.cookie,
        }
        all_products: list[dict[str, Any]] = []
        try:
            for location in CATEGORY_URLS:
                body = {
                    "Filters": [],
                    "IsSpecial": True,
                    "Location": location,
                    "PageNumber": 1,
                    "PageSize": 60,
                    "SearchTerm": "",
                    "SortType": "TraderRelevance",
                    "GroupEdmVariants": True,
                    "ExcludeSearchTypes": ["UntraceableVendors"],
                }
                async with websession.post(SEARCH_URL, headers=headers, json=body) as response:
                    if response.status in (401, 403):
                        raise UpdateFailed("Woolworths session cookie expired or was rejected")
                    response.raise_for_status()
                    all_products.extend(_products(await response.json()))
        except (ClientError, ValueError) as err:
            raise UpdateFailed(f"Unable to read Woolworths specials: {err}") from err

        unique: dict[str, dict[str, Any]] = {}
        for product in all_products:
            key = str(product.get("Stockcode") or product.get("stockcode") or product.get("Name"))
            name = str(product.get("Name") or product.get("DisplayName") or "").strip()
            if not name:
                continue
            unique[key] = {
                "name": name,
                "stockcode": product.get("Stockcode", product.get("stockcode")),
                "price": product.get("Price", product.get("price")),
                "was_price": product.get("WasPrice", product.get("was_price")),
                "promotion": product.get("PromotionDescription", product.get("promotion_text")),
                "url": product.get("UrlFriendlyName", ""),
                "terms": _terms(name),
            }
        products = list(unique.values())
        terms = sorted({term for product in products for term in product["terms"]})
        return {
            "products": products,
            "ingredient_terms": terms,
            "last_updated": self.last_update_success_time.isoformat() if self.last_update_success_time else None,
        }
