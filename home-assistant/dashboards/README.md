# Dashboard source

`meal-planner.yaml` is the Git-managed source of truth for the Kitchen Meal
Planner dashboard. Make dashboard changes here rather than in Home Assistant's
visual editor, then review and deploy them through the repository workflow.

The dashboard depends on entities and scripts defined in
`../packages/meal_planner.yaml`. In particular, its one-click generator writes
an automatic PASS/FAIL result to
`input_text.kitchen_meal_generator_status`.

The Generator preferences card controls the number of meals and the minimum
number categorized as Quick and Easy. The Quick and Easy minimum must not be
greater than the total meal count.

The initial dashboard shows only those preferences. Accepting them opens empty
meal slots; Randomize fills unlocked slots, while Reset clears the plan and
returns to setup. The final action locks every active meal and creates the
Mealie grocery list. Randomize and finalization buttons disable themselves and
show a spinner for as long as their Home Assistant scripts are running.
The shopping-list section also has a confirmation-protected clear action that
shows the same disabled spinner state while Mealie removes the items.

Each active slot uses the integration's searchable combobox card. Selecting an
existing name keeps its Mealie recipe link; free text creates a custom meal
that can be locked and later appears on the grocery list as
`Ingredients for <meal>`. Saved Mealie meals can also be opened from the picker;
custom meals intentionally have no recipe-detail action.
