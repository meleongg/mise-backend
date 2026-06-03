"""
Optimized Recipe Processing Pipeline
Fetches from TheMealDB → AI augments → generates embeddings → saves to DB in one pass
"""

import httpx
import os
import sys
import string
import json
import asyncio
from typing import Set, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from app.database import engine
from app.models import Recipe
from app.constants import THEMEALDB_BASE_URL, GENERATIVE_MODEL, EMBEDDING_MODEL
from app.utils.recipe_formatters import instructions_json_to_text
from app.utils.recipe_search_text import build_recipe_content_text


class InstructionStep(BaseModel):
    step: int = Field(description="Step number (1, 2, 3, etc.)")
    text: str = Field(
        description="Clear, concise instruction text with proper capitalization and punctuation"
    )


class RecipeMetadata(BaseModel):
    dietary_tags: List[str] = Field(
        description="List of ALL applicable dietary classifications. Must include at least one. Examples: vegetarian, vegan, gluten-free, dairy-free, pescatarian, keto, paleo, low-carb. If none apply specifically, use ['omnivore']"
    )
    allergens: List[str] = Field(
        description="List of ALL common allergens present in ingredients. Use empty list [] ONLY if truly allergen-free. Common allergens: nuts, peanuts, tree nuts, dairy, milk, eggs, soy, wheat, gluten, shellfish, fish, sesame"
    )
    portion_size: str = Field(
        description="Estimated serving size. REQUIRED - must provide even if not in source. Format: 'X servings' or 'X-Y people'. Examples: '4 servings', '6-8 people', '2-3 servings'"
    )
    prep_time_minutes: int = Field(
        description="Active preparation time in minutes (chopping, mixing, marinating). REQUIRED - estimate if not provided. Must be > 0. Range: 5-120 minutes typical",
        ge=1,
    )
    cook_time_minutes: int = Field(
        description="Cooking/baking time in minutes (oven, stovetop, grill time). REQUIRED - estimate if not provided. Can be 0 for no-cook recipes. Range: 0-300 minutes typical",
        ge=0,
    )
    skill_level_validated: str = Field(
        description="Difficulty level based on techniques required. MUST be exactly 'beginner', 'medium', or 'advanced' (lowercase). Beginner: basic techniques. Medium: some skill needed. Advanced: complex techniques"
    )
    instructions_formatted: List[InstructionStep] = Field(
        description="Step-by-step instructions reformatted as structured JSON. Each step must have sequential number and clear text. Remove existing numbering from text."
    )


# Initialize clients
llm = ChatOpenAI(model=GENERATIVE_MODEL, temperature=0.3)
embeddings_client = OpenAIEmbeddings(model=EMBEDDING_MODEL)


async def fetch_all_meal_ids() -> Set[str]:
    """Fetch all unique meal IDs from TheMealDB"""
    all_meal_ids: Set[str] = set()
    timeout = httpx.Timeout(10.0, connect=5.0)  # 10s timeout, 5s connect
    async with httpx.AsyncClient(timeout=timeout) as client:
        for letter in string.ascii_lowercase:
            url = f"{THEMEALDB_BASE_URL}/search.php?f={letter}"
            try:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                meals = data.get("meals", [])
                if meals:
                    for meal in meals:
                        if meal and meal.get("idMeal"):
                            all_meal_ids.add(meal["idMeal"])
            except Exception as e:
                print(f"⚠️  Failed to fetch letter {letter}: {e}")
    return all_meal_ids


async def fetch_recipe_details(meal_id: str) -> Optional[dict]:
    """Fetch full recipe details from TheMealDB"""
    url = f"{THEMEALDB_BASE_URL}/lookup.php?i={meal_id}"
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            meals = data.get("meals")  # Can be None, [], or [meal_obj]

            # Handle null response (deleted/invalid meal ID)
            if meals is None:
                print(f"⚠️  Meal {meal_id} not found (API returned null)")
                return None

            # Handle empty array
            if not meals or not meals[0]:
                print(f"⚠️  Meal {meal_id} returned empty data")
                return None

            return meals[0]
        except httpx.HTTPStatusError as e:
            print(
                f"⚠️  HTTP {e.response.status_code} fetching meal {meal_id}: {e.response.text[:100]}"
            )
            return None
        except httpx.TimeoutException:
            print(f"⚠️  Timeout fetching meal {meal_id}")
            return None
        except Exception as e:
            print(f"⚠️  Failed to fetch meal {meal_id}: {type(e).__name__}: {e}")
            return None


def parse_mealdb_ingredients(meal_data: dict) -> List[dict]:
    """Extract ingredients from TheMealDB format"""
    ingredients = []
    for i in range(1, 21):  # TheMealDB has up to 20 ingredients
        ingredient_raw = meal_data.get(f"strIngredient{i}")
        measure_raw = meal_data.get(f"strMeasure{i}")

        # Handle None values before calling .strip()
        ingredient = ingredient_raw.strip() if ingredient_raw else ""
        measure = measure_raw.strip() if measure_raw else ""

        if ingredient:
            ingredients.append({"name": ingredient, "measure": measure or "to taste"})
    return ingredients


async def augment_recipe_with_ai(
    meal_data: dict, ingredients: List[dict]
) -> Optional[RecipeMetadata]:
    """Use AI to augment recipe with metadata and formatted instructions"""
    try:
        ingredients_text = ", ".join(
            [f"{item['name']} ({item['measure']})" for item in ingredients]
        )

        system_prompt = """You are a professional chef and nutritionist analyzing recipes.
Your task is to provide COMPLETE metadata for every recipe - even if some information is missing from the source.

CRITICAL REQUIREMENTS:
1. ALL fields are REQUIRED - you must provide values even if you need to make reasonable inferences
2. If cuisine is missing: infer from recipe name, ingredients, or cooking techniques
3. If allergens are unclear: analyze ingredients carefully and list all potential allergens
4. If portion size is not stated: estimate based on ingredient quantities (e.g., "4 servings", "6-8 servings")
5. If times are missing: estimate based on recipe complexity and cooking methods
6. skill_level_validated must be EXACTLY one of: "beginner", "medium", or "advanced" (lowercase)

Be conservative with allergens - include anything that could cause reactions.
Provide realistic time estimates based on the complexity of the instructions."""

        user_prompt = f"""Analyze this recipe and provide COMPLETE metadata:

Recipe Name: {meal_data.get('strMeal', 'Unknown Recipe')}
Cuisine: {meal_data.get('strArea', 'Not specified - please infer from recipe')}
Ingredients: {ingredients_text}
Instructions: {meal_data.get('strInstructions')}

REQUIRED OUTPUT (all fields mandatory):
1. dietary_tags - Array of diet types this recipe fits (vegetarian, vegan, gluten-free, dairy-free, keto, paleo, etc.)
2. allergens - Array of ALL allergens present (nuts, dairy, eggs, soy, shellfish, fish, wheat, gluten, etc.)
3. portion_size - Serving size as string (e.g., "4 servings", "6-8 people", "2-3 servings")
4. prep_time_minutes - Active preparation time (chopping, mixing, etc.)
5. cook_time_minutes - Cooking/baking time (passive time in oven, stovetop, etc.)
6. skill_level_validated - Difficulty: "beginner", "medium", or "advanced"
7. instructions_formatted - Clean, numbered steps with proper formatting"""

        structured_llm = llm.with_structured_output(RecipeMetadata)
        metadata = structured_llm.invoke(
            [HumanMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )

        return metadata
    except Exception as e:
        print(f"⚠️  AI augmentation failed: {e}")
        return None


def generate_content_text(
    name: str,
    cuisine: str,
    ingredients: List[dict],
    instructions_json: List[dict],
    metadata: RecipeMetadata,
) -> str:
    """Generate content_text for embedding (includes augmented metadata)."""
    ingredients_text = " ".join(
        [f"{item['name']} {item['measure']}" for item in ingredients]
    )
    instructions_text = instructions_json_to_text(instructions_json)
    return build_recipe_content_text(
        name=name,
        cuisine=cuisine,
        ingredients_text=ingredients_text,
        instructions_text=instructions_text,
        dietary_tags=metadata.dietary_tags,
        allergens=metadata.allergens,
        portion_size=metadata.portion_size,
        prep_time_minutes=metadata.prep_time_minutes,
        cook_time_minutes=metadata.cook_time_minutes,
        skill_level_validated=metadata.skill_level_validated,
        difficulty=metadata.skill_level_validated,
    )


async def process_single_recipe(meal_id: str) -> bool:
    """Process one recipe: fetch → augment → embed → save"""
    # Create a new session for this recipe to avoid concurrent access issues
    db = Session(bind=engine)

    try:
        # Fetch from TheMealDB
        meal_data = await fetch_recipe_details(meal_id)
        if not meal_data:
            return False

        # Validate required fields
        if not meal_data.get("strInstructions") or not meal_data.get("strMeal"):
            print(
                f"⚠️  Recipe {meal_id} missing required fields (name or instructions)"
            )
            return False

        # Validate cuisine (required field in DB)
        cuisine = meal_data.get("strArea")
        if not cuisine or not cuisine.strip():
            print(f"⚠️  Recipe {meal_id} ({meal_data.get('strMeal')}) missing cuisine")
            return False

        # Parse ingredients
        ingredients = parse_mealdb_ingredients(meal_data)

        if not ingredients:
            print(
                f"⚠️  Recipe {meal_id} ({meal_data.get('strMeal')}) has no ingredients"
            )
            return False

        # AI augmentation
        metadata = await augment_recipe_with_ai(meal_data, ingredients)
        if not metadata:
            return False

        # Validate and normalize skill level
        valid_skills = {"beginner", "medium", "advanced"}
        if metadata.skill_level_validated.lower() not in valid_skills:
            print(
                f"⚠️  Invalid skill level '{metadata.skill_level_validated}' for {meal_data.get('strMeal')}, defaulting to 'medium'"
            )
            metadata.skill_level_validated = "medium"

        # Prepare instructions JSON
        instructions_json = [
            {"step": s.step, "text": s.text} for s in metadata.instructions_formatted
        ]

        # Generate content_text for embedding
        content_text = generate_content_text(
            meal_data.get("strMeal"),
            meal_data.get("strArea"),
            ingredients,
            instructions_json,
            metadata,
        )

        # Generate embedding
        embedding = embeddings_client.embed_query(content_text)

        # Save to database with ALL data at once
        recipe = Recipe(
            external_id=meal_data.get("idMeal"),
            name=meal_data.get("strMeal"),
            cuisine=meal_data.get("strArea"),
            ingredients=json.dumps(ingredients),
            instructions=json.dumps(instructions_json),
            difficulty=metadata.skill_level_validated,
            tags=meal_data.get("strTags"),
            image_url=meal_data.get("strMealThumb"),
            is_ai_generated=False,
            # Augmented metadata
            dietary_tags=json.dumps(metadata.dietary_tags),
            allergens=json.dumps(metadata.allergens),
            portion_size=metadata.portion_size,
            prep_time_minutes=metadata.prep_time_minutes,
            cook_time_minutes=metadata.cook_time_minutes,
            skill_level_validated=metadata.skill_level_validated,
            # Embedding fields
            content_text=content_text,
            embedding=embedding,
        )

        db.add(recipe)
        db.commit()

        print(
            f"✅ {meal_data.get('strMeal')} | {metadata.dietary_tags[:2]} | {metadata.prep_time_minutes}+{metadata.cook_time_minutes}min"
        )
        return True

    except Exception as e:
        print(f"❌ Failed to process {meal_id}: {type(e).__name__}: {str(e)[:100]}")
        db.rollback()
        return False
    finally:
        db.close()


async def process_all_recipes(batch_size: int = 5, limit: Optional[int] = None):
    """Main pipeline: fetch all recipes and process them"""
    db = Session(bind=engine)

    try:
        # Get all meal IDs from TheMealDB
        print("\n🔍 Fetching recipe list from TheMealDB...")
        all_meal_ids = await fetch_all_meal_ids()

        # Check what's already in DB
        existing_ids = set(
            str(external_id)
            for external_id in db.scalars(select(Recipe.external_id)).all()
            if external_id
        )

        ids_to_process = list(all_meal_ids - existing_ids)

        if limit:
            ids_to_process = ids_to_process[:limit]

        total = len(ids_to_process)
        print(f"\n📊 Found {len(all_meal_ids)} total recipes")
        print(f"   Already in DB: {len(existing_ids)}")
        print(f"   To process: {total}")
        print(f"   Batch size: {batch_size}\n")

        if total == 0:
            print("✅ All recipes already processed!")
            return

        success_count = 0
        fail_count = 0

        # Process in batches
        for i in range(0, total, batch_size):
            batch_ids = ids_to_process[i : i + batch_size]
            batch_num = i // batch_size + 1

            print(
                f"\n--- Batch {batch_num} ({i + 1}-{min(i + batch_size, total)} of {total}) ---"
            )

            # Process batch concurrently
            tasks = [process_single_recipe(meal_id) for meal_id in batch_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, bool) and result:
                    success_count += 1
                else:
                    fail_count += 1

            print(f"Progress: {success_count} ✅ | {fail_count} ❌")

        print(f"\n{'='*60}")
        print(f"🎉 Processing complete!")
        print(f"   Success: {success_count}/{total}")
        print(f"   Failed: {fail_count}/{total}")
        print(f"{'='*60}\n")

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Process recipes: fetch → augment → embed → save"
    )
    parser.add_argument(
        "--batch-size", type=int, default=5, help="Concurrent processing batch size"
    )
    parser.add_argument(
        "--limit", type=int, help="Limit recipes to process (for testing)"
    )

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"🚀 Optimized Recipe Processing Pipeline")
    print(f"{'='*60}")

    asyncio.run(process_all_recipes(batch_size=args.batch_size, limit=args.limit))
