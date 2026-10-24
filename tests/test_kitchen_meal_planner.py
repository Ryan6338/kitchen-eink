"""Tests for the pure-Python meal-plan selection engine."""

from __future__ import annotations

from datetime import date, timedelta
import importlib.util
from pathlib import Path
import random
import sys
import unittest


PLANNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "home-assistant"
    / "custom_components"
    / "kitchen_meal_planner"
    / "planner.py"
)
SPEC = importlib.util.spec_from_file_location("kitchen_meal_planner_engine", PLANNER_PATH)
assert SPEC and SPEC.loader
planner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = planner
SPEC.loader.exec_module(planner)


def recipe(
    slug: str,
    *,
    categories: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    ingredients: tuple[str, ...] = (),
    last_made: date | None = None,
):
    return planner.Recipe(
        slug=slug,
        name=slug.replace("-", " ").title(),
        categories=frozenset(categories),
        tags=frozenset(tags),
        ingredients=frozenset(ingredients),
        last_made=last_made,
    )


class MealPlannerTests(unittest.TestCase):
    def test_mealie_mapping_uses_categories_tags_and_food_names(self) -> None:
        mapped = planner.recipe_from_mapping(
            {
                "slug": "mapped-recipe",
                "name": "Mapped Recipe",
                "recipeCategory": [{"name": "Quick & Easy"}],
                "tags": [{"name": "Complex"}],
                "recipeIngredient": [
                    {"food": {"name": "Baby Spinach"}, "note": "washed"}
                ],
                "lastMade": "2026-10-22T08:30:00Z",
            }
        )

        self.assertTrue(mapped.is_quick_and_easy)
        self.assertTrue(mapped.is_complex)
        self.assertIn("baby spinach", mapped.ingredients)
        self.assertEqual(("Baby Spinach",), mapped.ingredient_lines)
        self.assertEqual(date(2026, 10, 22), mapped.last_made)

    def test_mealie_mapping_uses_home_assistant_ingredient_field(self) -> None:
        mapped = planner.recipe_from_mapping(
            {
                "slug": "service-recipe",
                "name": "Service Recipe",
                "ingredients": [
                    {
                        "display": "2 carrots, chopped",
                        "food": {"name": "Carrots"},
                    }
                ],
            }
        )

        self.assertIn("carrots", mapped.ingredients)
        self.assertEqual(("2 carrots, chopped",), mapped.ingredient_lines)

    def test_generation_enforces_hard_constraints(self) -> None:
        today = date(2026, 10, 24)
        recipes = [
            recipe("quick-chicken", categories=("quick and easy",), ingredients=("chicken", "spinach")),
            recipe("quick-fish", categories=("quick and easy",), ingredients=("fish", "lemon")),
            recipe("beef-tacos", ingredients=("beef", "capsicum")),
            recipe("pumpkin-pasta", ingredients=("pumpkin", "pasta")),
            recipe("complex-stew", tags=("complex",)),
            recipe("recent-curry", last_made=today - timedelta(days=2)),
            recipe("history-rice"),
        ]

        result = planner.generate_plan(
            recipes,
            count=4,
            minimum_quick=2,
            recent_slugs={"history-rice"},
            cutoff=today - timedelta(days=7),
            rng=random.Random(4),
        )

        selected = [meal.recipe for meal in result.meals]
        self.assertEqual(4, len({meal.slug for meal in selected}))
        self.assertGreaterEqual(sum(meal.is_quick_and_easy for meal in selected), 2)
        self.assertFalse(any(meal.is_complex for meal in selected))
        self.assertNotIn("recent-curry", {meal.slug for meal in selected})
        self.assertNotIn("history-rice", {meal.slug for meal in selected})

    def test_pantry_and_seasonal_terms_raise_score(self) -> None:
        preferred = recipe("spinach-chicken", ingredients=("spinach", "chicken"))
        other = recipe("beef-potato", ingredients=("beef", "potato"))

        preferred_score = planner.score_recipe(
            preferred,
            selected=[],
            seasonal_terms=("spinach",),
            use_up_terms=("chicken",),
        )
        other_score = planner.score_recipe(
            other,
            selected=[],
            seasonal_terms=("spinach",),
            use_up_terms=("chicken",),
        )

        self.assertGreater(preferred_score.total, other_score.total)

    def test_repeated_meal_style_is_penalized(self) -> None:
        selected = recipe("fried-rice", ingredients=("rice", "chicken"))
        another_rice_meal = recipe("rice-bowl", ingredients=("rice", "tofu"))
        pasta_meal = recipe("tomato-pasta", ingredients=("pasta", "tomato"))

        rice_score = planner.score_recipe(another_rice_meal, selected=[selected])
        pasta_score = planner.score_recipe(pasta_meal, selected=[selected])

        self.assertLess(rice_score.total, pasta_score.total)
        self.assertIn("rice", rice_score.repeated_styles)

    def test_generation_fails_without_two_quick_recipes(self) -> None:
        recipes = [
            recipe("only-quick", categories=("quick and easy",)),
            recipe("meal-two"),
            recipe("meal-three"),
            recipe("meal-four"),
        ]
        with self.assertRaisesRegex(planner.PlanningError, "Quick and Easy"):
            planner.generate_plan(
                recipes,
                count=4,
                minimum_quick=2,
                recent_slugs=set(),
                cutoff=date(2026, 10, 17),
                rng=random.Random(1),
            )

    def test_future_weeks_do_not_repeat_recipes(self) -> None:
        recipes = [
            recipe(f"quick-{number}", categories=("quick and easy",))
            for number in range(1, 5)
        ] + [recipe(f"regular-{number}") for number in range(1, 5)]

        result = planner.generate_future_plan(
            recipes,
            weeks=2,
            meals_per_week=4,
            minimum_quick_per_week=2,
            recent_slugs=set(),
            cutoff=date(2026, 10, 17),
            rng=random.Random(3),
        )

        slugs = [
            meal.recipe.slug
            for week in result.weeks
            for meal in week.meals
        ]
        self.assertEqual(8, len(slugs))
        self.assertEqual(8, len(set(slugs)))
        for week in result.weeks:
            self.assertGreaterEqual(
                sum(meal.recipe.is_quick_and_easy for meal in week.meals), 2
            )

    def test_replacement_preserves_quick_minimum(self) -> None:
        other_meals = [
            recipe("quick-one", categories=("quick and easy",)),
            recipe("regular-one"),
            recipe("regular-two"),
        ]
        recipes = other_meals + [
            recipe("quick-replacement", categories=("quick and easy",)),
            recipe("regular-replacement"),
        ]

        replacement = planner.replace_meal(
            recipes,
            selected=other_meals,
            minimum_quick=2,
            recent_slugs=set(),
            cutoff=date(2026, 10, 17),
            rng=random.Random(2),
        )

        self.assertTrue(replacement.recipe.is_quick_and_easy)

    def test_generation_preserves_locked_slots_and_randomizes_the_rest(self) -> None:
        locked = recipe(
            "locked-quick",
            categories=("quick and easy",),
            ingredients=("spinach",),
        )
        recipes = [
            locked,
            recipe("quick-two", categories=("quick and easy",)),
            recipe("quick-three", categories=("quick and easy",)),
            recipe("regular-one"),
            recipe("regular-two"),
            recipe("regular-three"),
        ]

        result = planner.generate_plan(
            recipes,
            count=4,
            minimum_quick=2,
            locked={2: locked},
            recent_slugs=set(),
            cutoff=date(2026, 10, 17),
            rng=random.Random(5),
        )

        self.assertEqual("locked-quick", result.meals[1].recipe.slug)
        self.assertEqual(4, len({meal.recipe.slug for meal in result.meals}))
        self.assertGreaterEqual(
            sum(meal.recipe.is_quick_and_easy for meal in result.meals), 2
        )

    def test_rerandomization_rotates_out_previous_unlocked_meals(self) -> None:
        recipes = [
            recipe(
                f"quick-{number}",
                categories=("quick and easy",),
            )
            for number in range(1, 5)
        ] + [recipe(f"regular-{number}") for number in range(1, 5)]
        previous = {"quick-1", "quick-2", "regular-1", "regular-2"}

        result = planner.generate_plan(
            recipes,
            count=4,
            minimum_quick=2,
            avoid_slugs=previous,
            recent_slugs=set(),
            cutoff=date(2026, 10, 17),
            rng=random.Random(7),
        )

        selected = {meal.recipe.slug for meal in result.meals}
        self.assertTrue(selected.isdisjoint(previous))
        self.assertEqual(4, result.avoided_previous_count)

    def test_custom_locked_meal_is_preserved_as_non_quick(self) -> None:
        custom = recipe("custom:family-takeaway")
        recipes = [
            recipe("quick-one", categories=("quick and easy",)),
            recipe("quick-two", categories=("quick and easy",)),
            recipe("regular-one"),
            recipe("regular-two"),
        ]

        result = planner.generate_plan(
            recipes,
            count=4,
            minimum_quick=2,
            locked={3: custom},
            recent_slugs=set(),
            cutoff=date(2026, 10, 17),
            rng=random.Random(9),
        )

        self.assertEqual("custom:family-takeaway", result.meals[2].recipe.slug)
        self.assertGreaterEqual(
            sum(meal.recipe.is_quick_and_easy for meal in result.meals), 2
        )


if __name__ == "__main__":
    unittest.main()
