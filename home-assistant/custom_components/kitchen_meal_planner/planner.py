"""Pure-Python recipe filtering and scoring for Kitchen Meal Planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import random
import re
from typing import Any, Iterable, Mapping, Sequence


QUICK_AND_EASY = "quick and easy"
COMPLEX = "complex"
CUSTOM_PREFIX = "custom:"

STYLE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("rice", "risotto", "biryani"),
    ("pasta", "spaghetti", "lasagne", "lasagna", "gnocchi"),
    ("noodle", "ramen"),
    ("curry", "dhal", "dal"),
    ("taco", "burrito", "enchilada", "mexican"),
    ("pizza", "flatbread"),
    ("soup", "stew"),
    ("salad",),
    ("burger", "sandwich", "wrap"),
)

OVERLAP_STOP_WORDS = frozenset(
    {
        "and",
        "fresh",
        "ground",
        "large",
        "medium",
        "olive",
        "optional",
        "pepper",
        "salt",
        "small",
        "taste",
        "water",
    }
)


class PlanningError(ValueError):
    """Raised when the requested plan cannot satisfy its hard constraints."""


@dataclass(frozen=True, slots=True)
class Recipe:
    """Normalized recipe data used by the planner."""

    slug: str
    name: str
    categories: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    ingredients: frozenset[str] = field(default_factory=frozenset)
    ingredient_lines: tuple[str, ...] = ()
    description: str = ""
    last_made: date | None = None

    @property
    def is_quick_and_easy(self) -> bool:
        return QUICK_AND_EASY in self.categories

    @property
    def is_complex(self) -> bool:
        return COMPLEX in self.tags

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.name.lower(),
                self.description.lower(),
                " ".join(sorted(self.categories)),
                " ".join(sorted(self.tags)),
                " ".join(sorted(self.ingredients)),
            )
        )

    @property
    def ingredient_tokens(self) -> frozenset[str]:
        tokens: set[str] = set()
        for ingredient in self.ingredients:
            normalized = normalize_label(ingredient)
            if normalized:
                tokens.add(normalized)
            tokens.update(
                word
                for word in normalized.split()
                if len(word) >= 4 and word not in OVERLAP_STOP_WORDS
            )
        return frozenset(tokens)


@dataclass(frozen=True, slots=True)
class Score:
    """Scoring explanation for one candidate."""

    total: float
    seasonal_matches: tuple[str, ...]
    use_up_matches: tuple[str, ...]
    shared_ingredients: tuple[str, ...]
    special_matches: tuple[str, ...]
    repeated_styles: tuple[str, ...]
    quick_bonus: int


@dataclass(frozen=True, slots=True)
class SelectedMeal:
    """A selected recipe and its score details."""

    recipe: Recipe
    score: Score


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Meal-plan result with selection diagnostics."""

    meals: tuple[SelectedMeal, ...]
    eligible_count: int
    quick_eligible_count: int
    excluded_complex_count: int
    excluded_recent_count: int
    avoided_previous_count: int = 0


@dataclass(frozen=True, slots=True)
class FuturePlanResult:
    """Several weekly plans with no recipe reused between weeks."""

    weeks: tuple[PlanResult, ...]


def normalize_label(value: Any) -> str:
    """Normalize a Mealie category, tag, or ingredient label."""
    text = str(value or "").strip().lower().replace("&", "and")
    text = re.sub(r"[_-]+", " ", text)
    return re.sub(r"\s+", " ", text)


def _named_values(values: Any) -> frozenset[str]:
    output: set[str] = set()
    for value in values or []:
        if isinstance(value, Mapping):
            label = value.get("name") or value.get("slug")
        else:
            label = value
        normalized = normalize_label(label)
        if normalized:
            output.add(normalized)
    return frozenset(output)


def _ingredient_values(values: Any) -> frozenset[str]:
    output: set[str] = set()
    for value in values or []:
        if not isinstance(value, Mapping):
            normalized = normalize_label(value)
            if normalized:
                output.add(normalized)
            continue

        food = value.get("food")
        has_food_name = False
        if isinstance(food, Mapping):
            food_name = normalize_label(food.get("name"))
            if food_name:
                output.add(food_name)
                has_food_name = True

        if not has_food_name:
            fallback = (
                value.get("display")
                or value.get("original_text")
                or value.get("originalText")
                or value.get("note")
            )
            normalized = normalize_label(fallback)
            if normalized:
                output.add(normalized)
    return frozenset(output)


def _ingredient_lines(values: Any) -> tuple[str, ...]:
    output: list[str] = []
    for value in values or []:
        if not isinstance(value, Mapping):
            line = str(value or "").strip()
        else:
            food = value.get("food")
            food_name = food.get("name") if isinstance(food, Mapping) else None
            line = str(
                value.get("display")
                or value.get("original_text")
                or value.get("originalText")
                or food_name
                or value.get("note")
                or ""
            ).strip()
        if line:
            output.append(line)
    return tuple(output)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def recipe_from_mapping(data: Mapping[str, Any]) -> Recipe:
    """Create a normalized recipe from Mealie's snake- or camel-case data."""
    slug = str(data.get("slug") or data.get("id") or "")
    name = str(data.get("name") or slug or "Unnamed recipe")
    ingredients = (
        data.get("recipe_ingredient")
        or data.get("recipeIngredient")
        or data.get("ingredients")
    )
    return Recipe(
        slug=slug,
        name=name,
        categories=_named_values(
            data.get("recipe_category")
            or data.get("recipeCategory")
            or data.get("categories")
        ),
        tags=_named_values(data.get("tags")),
        ingredients=_ingredient_values(ingredients),
        ingredient_lines=_ingredient_lines(ingredients),
        description=str(data.get("description") or ""),
        last_made=_parse_date(data.get("last_made") or data.get("lastMade")),
    )


def _matching_terms(recipe: Recipe, terms: Iterable[str]) -> tuple[str, ...]:
    text = recipe.search_text
    return tuple(sorted({term for term in terms if term and term in text}))


def _styles(recipe: Recipe) -> frozenset[int]:
    text = recipe.search_text
    return frozenset(
        index
        for index, terms in enumerate(STYLE_GROUPS)
        if any(term in text for term in terms)
    )


def score_recipe(
    recipe: Recipe,
    *,
    selected: Sequence[Recipe],
    seasonal_terms: Iterable[str] = (),
    use_up_terms: Iterable[str] = (),
    special_terms: Iterable[str] = (),
    use_specials: bool = False,
    jitter: float = 0.0,
) -> Score:
    """Score a candidate against preferences and already selected meals."""
    seasonal = _matching_terms(recipe, seasonal_terms)
    use_up = _matching_terms(recipe, use_up_terms)
    specials = _matching_terms(recipe, special_terms) if use_specials else ()

    selected_tokens: set[str] = set()
    selected_styles: set[int] = set()
    for selected_recipe in selected:
        selected_tokens.update(selected_recipe.ingredient_tokens)
        selected_styles.update(_styles(selected_recipe))

    shared = tuple(sorted(recipe.ingredient_tokens & selected_tokens))
    repeated_style_ids = sorted(_styles(recipe) & selected_styles)
    repeated_styles = tuple(STYLE_GROUPS[index][0] for index in repeated_style_ids)
    quick_bonus = 2 if recipe.is_quick_and_easy else 0
    total = (
        len(seasonal) * 3
        + len(use_up) * 6
        + len(shared) * 2
        + len(specials) * 3
        + quick_bonus
        - len(repeated_styles) * 12
        + jitter
    )
    return Score(
        total=total,
        seasonal_matches=seasonal,
        use_up_matches=use_up,
        shared_ingredients=shared,
        special_matches=specials,
        repeated_styles=repeated_styles,
        quick_bonus=quick_bonus,
    )


def _choose_weighted(
    scored: Sequence[tuple[Recipe, Score]], rng: random.Random
) -> tuple[Recipe, Score]:
    ranked = sorted(scored, key=lambda item: item[1].total, reverse=True)
    best_score = ranked[0][1].total
    competitive = [item for item in ranked if item[1].total >= best_score - 3][:10]
    minimum = min(item[1].total for item in competitive)
    weights = [max(1.0, item[1].total - minimum + 1.0) for item in competitive]
    return rng.choices(competitive, weights=weights, k=1)[0]


def _eligible_recipes(
    recipes: Sequence[Recipe], *, recent_slugs: set[str], cutoff: date
) -> tuple[list[Recipe], int, int]:
    eligible: list[Recipe] = []
    complex_count = 0
    recent_count = 0
    for recipe in recipes:
        if not recipe.slug:
            continue
        if recipe.is_complex:
            complex_count += 1
            continue
        if recipe.slug in recent_slugs or (
            recipe.last_made is not None and recipe.last_made >= cutoff
        ):
            recent_count += 1
            continue
        eligible.append(recipe)
    return eligible, complex_count, recent_count


def generate_plan(
    recipes: Sequence[Recipe],
    *,
    count: int,
    minimum_quick: int,
    recent_slugs: Iterable[str],
    cutoff: date,
    locked: Mapping[int, Recipe] | None = None,
    avoid_slugs: Iterable[str] = (),
    seasonal_terms: Iterable[str] = (),
    use_up_terms: Iterable[str] = (),
    special_terms: Iterable[str] = (),
    use_specials: bool = False,
    limit_quick_to_minimum: bool = False,
    rng: random.Random | None = None,
) -> PlanResult:
    """Generate a plan satisfying all hard constraints."""
    if count < 1:
        raise PlanningError("Meal count must be at least one")
    if minimum_quick < 0 or minimum_quick > count:
        raise PlanningError("Quick and Easy minimum must fit within meal count")

    randomizer = rng or random.SystemRandom()
    locked_by_slot = dict(locked or {})
    invalid_slots = [slot for slot in locked_by_slot if slot < 1 or slot > count]
    if invalid_slots:
        raise PlanningError(
            f"Locked meal slot {invalid_slots[0]} is outside the active plan"
        )
    locked_slugs = [recipe.slug for recipe in locked_by_slot.values()]
    if len(locked_slugs) != len(set(locked_slugs)):
        raise PlanningError("Locked meals must be unique")

    eligible, complex_count, recent_count = _eligible_recipes(
        recipes, recent_slugs=set(recent_slugs), cutoff=cutoff
    )
    eligible_slugs = {recipe.slug for recipe in eligible}
    for slot, recipe in locked_by_slot.items():
        if recipe.slug not in eligible_slugs and not recipe.slug.startswith(CUSTOM_PREFIX):
            raise PlanningError(
                f"Locked meal {slot} is no longer eligible; unlock it and try again"
            )

    quick_count = sum(recipe.is_quick_and_easy for recipe in eligible)
    eligible_unlocked = [
        recipe for recipe in eligible if recipe.slug not in set(locked_slugs)
    ]
    total_available = len(eligible_unlocked) + len(locked_by_slot)
    if total_available < count:
        raise PlanningError(
            f"Need {count} eligible recipes but found {total_available}"
        )
    quick_possible = sum(
        recipe.is_quick_and_easy for recipe in eligible_unlocked
    ) + sum(recipe.is_quick_and_easy for recipe in locked_by_slot.values())
    if quick_possible < minimum_quick:
        raise PlanningError(
            f"Need {minimum_quick} Quick and Easy recipes but found {quick_possible}"
        )

    selected_by_slot: dict[int, SelectedMeal] = {}
    selected_recipes = list(locked_by_slot.values())
    used = set(locked_slugs)
    for slot, recipe in locked_by_slot.items():
        selected_by_slot[slot] = SelectedMeal(
            recipe=recipe,
            score=score_recipe(
                recipe,
                selected=[item for item in selected_recipes if item.slug != recipe.slug],
                seasonal_terms=seasonal_terms,
                use_up_terms=use_up_terms,
                special_terms=special_terms,
                use_specials=use_specials,
            ),
        )

    open_slots = [slot for slot in range(1, count + 1) if slot not in locked_by_slot]
    quick_locked = sum(recipe.is_quick_and_easy for recipe in locked_by_slot.values())
    quick_needed = max(0, minimum_quick - quick_locked)
    if quick_needed > len(open_slots):
        raise PlanningError("Locked meals leave too few slots for the Quick and Easy minimum")

    avoided = set(avoid_slugs) - used
    fresh_candidates = [recipe for recipe in eligible if recipe.slug not in avoided]
    fresh_available = [recipe for recipe in fresh_candidates if recipe.slug not in used]
    if (
        len(fresh_available) >= len(open_slots)
        and sum(recipe.is_quick_and_easy for recipe in fresh_available) >= quick_needed
    ):
        candidate_pool = fresh_candidates
        avoided_previous_count = len(
            {recipe.slug for recipe in eligible if recipe.slug in avoided}
        )
    else:
        candidate_pool = eligible
        avoided_previous_count = 0

    for open_index, slot in enumerate(open_slots):
        require_quick = open_index < quick_needed
        candidates = [
            recipe
            for recipe in candidate_pool
            if recipe.slug not in used
            and (not require_quick or recipe.is_quick_and_easy)
        ]
        if limit_quick_to_minimum and not require_quick:
            non_quick_candidates = [
                recipe for recipe in candidates if not recipe.is_quick_and_easy
            ]
            if non_quick_candidates:
                candidates = non_quick_candidates
        scored = [
            (
                recipe,
                score_recipe(
                    recipe,
                    selected=selected_recipes,
                    seasonal_terms=seasonal_terms,
                    use_up_terms=use_up_terms,
                    special_terms=special_terms,
                    use_specials=use_specials,
                    jitter=randomizer.random() * 3,
                ),
            )
            for recipe in candidates
        ]
        if not scored:
            raise PlanningError("No candidate can satisfy the remaining constraints")
        recipe, score = _choose_weighted(scored, randomizer)
        selected_by_slot[slot] = SelectedMeal(recipe=recipe, score=score)
        selected_recipes.append(recipe)
        used.add(recipe.slug)

    return PlanResult(
        meals=tuple(selected_by_slot[slot] for slot in range(1, count + 1)),
        eligible_count=len(eligible),
        quick_eligible_count=quick_count,
        excluded_complex_count=complex_count,
        excluded_recent_count=recent_count,
        avoided_previous_count=avoided_previous_count,
    )


def replace_meal(
    recipes: Sequence[Recipe],
    *,
    selected: Sequence[Recipe],
    minimum_quick: int,
    recent_slugs: Iterable[str],
    cutoff: date,
    seasonal_terms: Iterable[str] = (),
    use_up_terms: Iterable[str] = (),
    special_terms: Iterable[str] = (),
    use_specials: bool = False,
    rng: random.Random | None = None,
) -> SelectedMeal:
    """Choose one replacement while preserving plan constraints."""
    randomizer = rng or random.SystemRandom()
    eligible, _, _ = _eligible_recipes(
        recipes, recent_slugs=set(recent_slugs), cutoff=cutoff
    )
    used = {recipe.slug for recipe in selected}
    quick_selected = sum(recipe.is_quick_and_easy for recipe in selected)
    quick_needed = max(0, minimum_quick - quick_selected)
    if quick_needed > 1:
        raise PlanningError(
            "One replacement cannot restore the Quick and Easy minimum"
        )

    candidates = [
        recipe
        for recipe in eligible
        if recipe.slug not in used
        and (quick_needed == 0 or recipe.is_quick_and_easy)
    ]
    scored = [
        (
            recipe,
            score_recipe(
                recipe,
                selected=selected,
                seasonal_terms=seasonal_terms,
                use_up_terms=use_up_terms,
                special_terms=special_terms,
                use_specials=use_specials,
                jitter=randomizer.random() * 3,
            ),
        )
        for recipe in candidates
    ]
    if not scored:
        raise PlanningError("No eligible replacement recipe was found")
    recipe, score = _choose_weighted(scored, randomizer)
    return SelectedMeal(recipe=recipe, score=score)


def generate_future_plan(
    recipes: Sequence[Recipe],
    *,
    weeks: int,
    meals_per_week: int,
    minimum_quick_per_week: int,
    recent_slugs: Iterable[str],
    cutoff: date,
    seasonal_terms: Iterable[str] = (),
    use_up_terms: Iterable[str] = (),
    special_terms: Iterable[str] = (),
    use_specials: bool = False,
    rng: random.Random | None = None,
) -> FuturePlanResult:
    """Generate several unique weekly plans using the same scoring rules."""
    if weeks < 1:
        raise PlanningError("Week count must be at least one")

    randomizer = rng or random.SystemRandom()
    unavailable = set(recent_slugs)
    results: list[PlanResult] = []
    for _ in range(weeks):
        result = generate_plan(
            recipes,
            count=meals_per_week,
            minimum_quick=minimum_quick_per_week,
            recent_slugs=unavailable,
            cutoff=cutoff,
            seasonal_terms=seasonal_terms,
            use_up_terms=use_up_terms,
            special_terms=special_terms,
            use_specials=use_specials,
            limit_quick_to_minimum=True,
            rng=randomizer,
        )
        results.append(result)
        unavailable.update(meal.recipe.slug for meal in result.meals)
    return FuturePlanResult(weeks=tuple(results))
