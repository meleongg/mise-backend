#!/usr/bin/env python3
"""
Seed a small Mise-owned recipe catalog via LLM structured output (no TheMealDB).

Generates ~N recipes per cuisine in CUISINE_OPTIONS with numeric portion_size,
parse-friendly ingredients, embeddings, and is_ai_generated=True.
Images are left empty for scripts/backfill_recipe_images.py (Pexels).

Requires DATABASE_URL and OPENAI_API_KEY (e.g. backend/.env).

Examples:
  python scripts/seed_llm_catalog.py --dry-run
  python scripts/seed_llm_catalog.py --per-cuisine 5
  python scripts/seed_llm_catalog.py --per-cuisine 2 --cuisine Chinese
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import List, Literal, Optional

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.constants import CUISINE_OPTIONS, EMBEDDING_MODEL, GENERATIVE_MODEL
from app.database import SessionLocal
from app.models import Recipe
from app.utils.recipe_formatters import instructions_json_to_text
from app.utils.recipe_search_text import build_recipe_content_text

load_dotenv(os.path.join(project_root, ".env"))


class IngredientItem(BaseModel):
    name: str = Field(description="Ingredient name only (e.g. yellow onion)")
    measure: str = Field(
        description=(
            "Parseable amount + unit when possible (e.g. '2 cups', '1 tbsp', '400 g'). "
            "Avoid vague-only measures when a quantity exists."
        )
    )


class InstructionStep(BaseModel):
    step: int = Field(description="1-based step number")
    text: str = Field(description="Clear instruction sentence(s)")


class GeneratedRecipe(BaseModel):
    name: str = Field(description="Dish name without marketing fluff")
    cuisine: str
    ingredients: List[IngredientItem] = Field(min_length=4)
    instructions: List[InstructionStep] = Field(min_length=3)
    difficulty: Literal["easy", "medium", "hard"]
    skill_level_validated: Literal["beginner", "medium", "advanced"]
    dietary_tags: List[str] = Field(
        description="e.g. vegetarian, vegan, gluten-free, omnivore"
    )
    allergens: List[str] = Field(
        description="Common allergens present; empty list if none"
    )
    portion_size: float = Field(gt=0, description="Single positive serving count")
    prep_time_minutes: int = Field(ge=1)
    cook_time_minutes: int = Field(ge=0)
    tags: List[str] = Field(default_factory=list)


SKILL_CYCLE = ["beginner", "medium", "advanced", "beginner", "medium"]
DIFFICULTY_FOR_SKILL = {
    "beginner": "easy",
    "medium": "medium",
    "advanced": "hard",
}
DIET_HINTS = [
    "omnivore weeknight classic",
    "vegetarian-friendly",
    "includes a common allergen (call it out honestly in allergens)",
    "gluten-conscious option when natural for the cuisine",
    "simple pantry-forward dish",
]


def _llm() -> ChatOpenAI:
    return ChatOpenAI(model=GENERATIVE_MODEL, temperature=0.4)


def generate_recipe(cuisine: str, index: int, *, avoid_names: List[str]) -> GeneratedRecipe:
    skill = SKILL_CYCLE[index % len(SKILL_CYCLE)]
    difficulty = DIFFICULTY_FOR_SKILL[skill]
    diet_hint = DIET_HINTS[index % len(DIET_HINTS)]
    avoid = ", ".join(avoid_names) if avoid_names else "(none yet)"
    prompt = f"""Create ONE original home-cook recipe for cuisine={cuisine}.

Constraints:
- skill_level_validated must be "{skill}" and difficulty must be "{difficulty}"
- Variety hint: {diet_hint}
- portion_size: a single positive number (typically 2, 4, or 6) — never a range
- Every ingredient measure should be shopping-scale friendly ("2 cups", "1 tbsp")
- Do not reuse these names: {avoid}
- Practical weeknight or weekend home cooking; no trademarked restaurant dishes
- Return only structured fields requested by the schema
"""
    structured = _llm().with_structured_output(GeneratedRecipe)
    recipe = structured.invoke([HumanMessage(content=prompt)])
    recipe.cuisine = cuisine
    recipe.skill_level_validated = skill
    recipe.difficulty = difficulty
    return recipe


def persist_recipe(db: Session, generated: GeneratedRecipe, embeddings: OpenAIEmbeddings) -> Recipe:
    ingredients_json = json.dumps(
        [{"name": i.name, "measure": i.measure} for i in generated.ingredients]
    )
    instructions_json = json.dumps(
        [{"step": s.step, "text": s.text} for s in generated.instructions]
    )
    content_text = build_recipe_content_text(
        name=generated.name,
        cuisine=generated.cuisine,
        ingredients_text=", ".join(
            f"{i.measure} {i.name}".strip() for i in generated.ingredients
        ),
        instructions_text=instructions_json_to_text(instructions_json),
        dietary_tags=generated.dietary_tags,
        allergens=generated.allergens,
        portion_size=generated.portion_size,
        prep_time_minutes=generated.prep_time_minutes,
        cook_time_minutes=generated.cook_time_minutes,
        skill_level_validated=generated.skill_level_validated,
        difficulty=generated.difficulty,
    )
    embedding = embeddings.embed_query(content_text)
    recipe = Recipe(
        id=uuid.uuid4(),
        name=generated.name.strip(),
        cuisine=generated.cuisine,
        ingredients=ingredients_json,
        instructions=instructions_json,
        difficulty=generated.difficulty,
        tags=json.dumps(generated.tags or []),
        image_url=None,
        dietary_tags=json.dumps(generated.dietary_tags),
        allergens=json.dumps(generated.allergens),
        portion_size=float(generated.portion_size),
        prep_time_minutes=generated.prep_time_minutes,
        cook_time_minutes=generated.cook_time_minutes,
        skill_level_validated=generated.skill_level_validated,
        content_text=content_text,
        embedding=embedding,
        is_ai_generated=True,
    )
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed LLM Mise catalog recipes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--per-cuisine", type=int, default=5)
    parser.add_argument(
        "--cuisine",
        action="append",
        dest="cuisines",
        help="Limit to cuisine(s); default all CUISINE_OPTIONS",
    )
    args = parser.parse_args()

    cuisines = args.cuisines or list(CUISINE_OPTIONS)
    for c in cuisines:
        if c not in CUISINE_OPTIONS:
            print(f"Unknown cuisine {c!r}; allowed: {CUISINE_OPTIONS}", file=sys.stderr)
            return 1
    if args.per_cuisine < 1:
        print("--per-cuisine must be >= 1", file=sys.stderr)
        return 1

    plan = [(c, i) for c in cuisines for i in range(args.per_cuisine)]
    print(f"Will generate {len(plan)} recipes across {cuisines}")
    if args.dry_run:
        for cuisine, idx in plan:
            print(
                f"  - {cuisine} #{idx + 1} "
                f"(skill={SKILL_CYCLE[idx % len(SKILL_CYCLE)]}, hint={DIET_HINTS[idx % len(DIET_HINTS)]})"
            )
        print("(dry-run: no LLM calls / no DB writes)")
        return 0

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY required", file=sys.stderr)
        return 1

    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    created = 0
    with SessionLocal() as db:
        names_by_cuisine: dict[str, list[str]] = {c: [] for c in cuisines}
        for cuisine, idx in plan:
            print(f"Generating {cuisine} #{idx + 1}...")
            generated = generate_recipe(
                cuisine, idx, avoid_names=names_by_cuisine[cuisine]
            )
            recipe = persist_recipe(db, generated, embeddings)
            names_by_cuisine[cuisine].append(recipe.name)
            created += 1
            print(
                f"  saved {recipe.id} | {recipe.name} | "
                f"portion={recipe.portion_size} | {recipe.skill_level_validated}"
            )

    print(f"Done. Created {created} recipes. Run backfill_recipe_images.py next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
