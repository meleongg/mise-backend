"""NewRecipeSchema includes the same metadata fields persisted on Recipe rows."""

from app.schemas.adaptive_planner import NewRecipeSchema


def test_new_recipe_schema_has_recipe_metadata_fields():
    fields = set(NewRecipeSchema.model_fields.keys())
    assert {
        "name",
        "cuisine",
        "ingredients",
        "instructions",
        "difficulty",
        "dietary_tags",
        "allergens",
        "portion_size",
        "prep_time_minutes",
        "cook_time_minutes",
        "skill_level_validated",
    }.issubset(fields)


def test_new_recipe_schema_parses_full_payload():
    recipe = NewRecipeSchema.model_validate(
        {
            "name": "Tomato Basil Pasta",
            "cuisine": "Italian",
            "ingredients": [{"name": "Pasta", "measure": "200g"}],
            "instructions": [{"step": 1, "text": "Boil pasta."}],
            "difficulty": "easy",
            "dietary_tags": ["vegetarian"],
            "allergens": ["wheat"],
            "portion_size": "4 servings",
            "prep_time_minutes": 10,
            "cook_time_minutes": 15,
            "skill_level_validated": "beginner",
        }
    )
    assert recipe.prep_time_minutes == 10
    assert recipe.dietary_tags == ["vegetarian"]
