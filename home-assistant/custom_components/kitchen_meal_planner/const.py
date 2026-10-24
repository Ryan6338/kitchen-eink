"""Constants for the Kitchen Meal Planner integration."""

DOMAIN = "kitchen_meal_planner"

SERVICE_GENERATE_PLAN = "generate_plan"
SERVICE_GENERATE_FUTURE_PLAN = "generate_future_plan"
SERVICE_REPLACE_MEAL = "replace_meal"
SERVICE_SYNC_PLAN = "sync_plan"
SERVICE_LOAD_CATALOG = "load_catalog"
SERVICE_SET_MEAL = "set_meal"
SERVICE_POPULATE_SHOPPING_LIST = "populate_shopping_list"
SERVICE_CLEAR_SHOPPING_LIST = "clear_shopping_list"

SENSOR_ENTITY_ID = "sensor.kitchen_meal_plan_generator"
FUTURE_SENSOR_ENTITY_ID = "sensor.kitchen_future_meal_plan_generator"
CATALOG_SENSOR_ENTITY_ID = "sensor.kitchen_meal_recipe_catalog"
STATUS_ENTITY_ID = "input_text.kitchen_meal_generator_status"

DEFAULT_MEAL_COUNT = 4
DEFAULT_MINIMUM_QUICK = 2
DEFAULT_CACHE_MINUTES = 60
