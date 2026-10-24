# Kitchen Meal Planner integration

This custom integration contains the meal-selection engine. It reuses Home
Assistant's configured Mealie client, so it does not store a second URL or API
token.

It exposes eight actions:

- `kitchen_meal_planner.load_catalog`
- `kitchen_meal_planner.set_meal`
- `kitchen_meal_planner.populate_shopping_list`
- `kitchen_meal_planner.clear_shopping_list`
- `kitchen_meal_planner.generate_plan`
- `kitchen_meal_planner.generate_future_plan`
- `kitchen_meal_planner.replace_meal`
- `kitchen_meal_planner.sync_plan`

Recipe details are fetched with bounded concurrency and cached for one hour.
The Python engine applies hard eligibility rules before weighted random
selection, and publishes the compact plan and scoring diagnostics on
`sensor.kitchen_meal_plan_generator`.

Shopping-list generation reuses those cached recipe details, filters pantry
staples, adds unique ingredient lines with bounded concurrency, and refreshes
the Mealie list once after all writes complete. Clearing the list also uses
bounded concurrency, verifies removals after a refresh, and safely retries
items that remain.

When a meal slot's lock helper is on, `generate_plan` preserves that recipe and
randomizes only the remaining slots. Locked recipes still count toward the
configured Quick and Easy minimum and must remain eligible.

On consecutive runs, recipes currently occupying unlocked slots are rotated
out when the remaining catalogue can still satisfy the meal-count and Quick
and Easy constraints. This prevents a highly weighted recipe from immediately
winning every reroll while retaining a safe fallback for small catalogues.

The integration also serves the dashboard's searchable meal-picker card. An
exact catalogue match stores the Mealie recipe slug; any other text is stored
as a custom meal. Locked custom meals survive rerolls and produce an
`Ingredients for <meal>` placeholder in the grocery list.

The compatibility scripts in `../../packages/meal_planner.yaml` preserve the
script entity IDs used by the dashboard and ESPHome display.
