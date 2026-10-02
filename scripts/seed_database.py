#!/usr/bin/env python3
"""
Database Seed Script for ChefPath Backend

This script populates the database with sample data for testing and development.
It creates sample users, creates mock recipes, and sets up weekly plans.

Usage:
    python scripts/seed_database.py
    python scripts/seed_database.py --clear  # Clear database first
"""

import sys
import asyncio
import os
import uuid
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from app.services.weekly_plan import replace_plan_catalog_entries

load_dotenv()

TEST_EMAIL = os.environ.get("TEST_EMAIL")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD")

# Add the backend directory to the Python path
backend_dir = Path(__file__).parent.parent

sys.path.insert(0, str(backend_dir))

from sqlalchemy.orm import Session
from app.database import engine, create_tables
from app.models import User, Recipe, WeeklyPlan, UserRecipeProgress
from app.services.weekly_plan import WeeklyPlanService
from app.utils.password import hash_password


async def seed_database(clear_first: bool = False):
    """Seed database with only the Test user and a weekly plan"""
    print("🌱 Seeding ChefPath Database with Test user...")

    # Clear database if requested
    if clear_first:
        from scripts.clear_database import clear_database

        print("Clearing existing data first...")
        clear_database()

    # Ensure tables exist
    create_tables()

    # Create database session
    db = Session(bind=engine)

    # Initialize services
    plan_service = WeeklyPlanService()

    try:
        # Create only the Test user (with a generated UUID)
        print("\n� Creating Test user...")
        test_user = User(
            id=uuid.uuid4(),
            first_name="First",
            last_name="Last",
            email=TEST_EMAIL,
            hashed_password=hash_password(
                TEST_PASSWORD
            ),  # Set a known password for login testing
            cuisine="Chinese",
            frequency=3,
            skill_level="beginner",
            user_goal="Master a Cuisine",
            created_at=datetime(2025, 9, 29, 2, 42, 52, tzinfo=timezone.utc),
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)
        print(f"  ✅ Created Test user: {test_user.email} (ID: {test_user.id})")

        # Manually create mock Chinese recipes
        print(f"\n🍽️  Creating mock Chinese recipes...")
        mock_recipes_data = [
            {
                "name": "Mock Sweet and Sour Pork",
                "cuisine": "Chinese",
                "ingredients": json.dumps(
                    [
                        {"name": "pork", "measure": "300g"},
                        {"name": "pineapple", "measure": "100g"},
                        {"name": "bell pepper", "measure": "1"},
                        {"name": "vinegar", "measure": "2 tbsp"},
                        {"name": "sugar", "measure": "2 tbsp"},
                    ]
                ),
                "instructions": "1. Fry pork. 2. Add sauce. 3. Serve.",
                "difficulty": "easy",
                "image_url": "https://example.com/sweet_sour_pork.jpg",
                "content_text": 'Recipe Name: Mock Sweet and Sour Pork Cuisine: Chinese Difficulty: easy Tags: ["pork", "sweet", "sour"] Ingredients: [{"name": "pork", "measure": "300g"}, {"name": "pineapple", "measure": "100g"}, {"name": "bell pepper", "measure": "1"}, {"name": "vinegar", "measure": "2 tbsp"}, {"name": "sugar", "measure": "2 tbsp"}] Instructions: 1. Fry pork. 2. Add sauce. 3. Serve.',
            },
            {
                "name": "Mock Kung Pao Chicken",
                "cuisine": "Chinese",
                "ingredients": json.dumps(
                    [
                        {"name": "chicken", "measure": "250g"},
                        {"name": "peanuts", "measure": "50g"},
                        {"name": "chili peppers", "measure": "3"},
                        {"name": "soy sauce", "measure": "2 tbsp"},
                    ]
                ),
                "instructions": "1. Stir fry chicken. 2. Add peanuts and sauce. 3. Serve.",
                "difficulty": "medium",
                "image_url": "https://example.com/kung_pao_chicken.jpg",
                "content_text": 'Recipe Name: Mock Kung Pao Chicken Cuisine: Chinese Difficulty: medium Tags: ["chicken", "spicy"] Ingredients: [{"name": "chicken", "measure": "250g"}, {"name": "peanuts", "measure": "50g"}, {"name": "chili peppers", "measure": "3"}, {"name": "soy sauce", "measure": "2 tbsp"}] Instructions: 1. Stir fry chicken. 2. Add peanuts and sauce. 3. Serve.',
            },
            {
                "name": "Mock Mapo Tofu",
                "cuisine": "Chinese",
                "ingredients": json.dumps(
                    [
                        {"name": "tofu", "measure": "400g"},
                        {"name": "ground pork", "measure": "100g"},
                        {"name": "chili bean paste", "measure": "1 tbsp"},
                        {"name": "green onion", "measure": "2"},
                    ]
                ),
                "instructions": "1. Cook pork. 2. Add tofu and sauce. 3. Simmer.",
                "difficulty": "easy",
                "image_url": "https://example.com/mapo_tofu.jpg",
                "content_text": 'Recipe Name: Mock Mapo Tofu Cuisine: Chinese Difficulty: easy Tags: ["tofu", "spicy", "vegetarian"] Ingredients: [{"name": "tofu", "measure": "400g"}, {"name": "ground pork", "measure": "100g"}, {"name": "chili bean paste", "measure": "1 tbsp"}, {"name": "green onion", "measure": "2"}] Instructions: 1. Cook pork. 2. Add tofu and sauce. 3. Simmer.',
            },
        ]
        recipes = []
        for recipe_data in mock_recipes_data:
            recipe = Recipe(id=uuid.uuid4(), **recipe_data)
            db.add(recipe)
            db.commit()
            db.refresh(recipe)
            recipes.append(recipe)
        print(f"    ✅ Added {len(recipes)} mock Chinese recipes")

        # Mock week 1 plan for Test user using static recipes
        print(f"\n📅 Generating week 1 plan for Test user (mocked)...")
        plan = WeeklyPlan(
            user_id=test_user.id,
            week_number=1,
            is_unlocked=True,
        )
        db.add(plan)
        db.flush()
        replace_plan_catalog_entries(plan, recipes, db)
        db.commit()
        db.refresh(plan)
        recipe_ids = [str(recipe.id) for recipe in recipes]
        print(f"  ✅ Created week 1 plan with entries for recipes: {recipe_ids}")
        # Seed UserRecipeProgress for each recipe in the weekly plan
        print(f"\n📝 Seeding UserRecipeProgress records...")
        for i, recipe in enumerate(recipes):
            progress_id = uuid.uuid4()
            if i == 0:
                # Mark first recipe as completed with feedback
                progress = UserRecipeProgress(
                    id=progress_id,
                    user_id=test_user.id,
                    recipe_id=recipe.id,
                    week_number=1,
                    status="completed",
                    feedback="just_right",
                    completed_at=datetime.now(timezone.utc),
                )
            else:
                # Others as not started
                progress = UserRecipeProgress(
                    id=progress_id,
                    user_id=test_user.id,
                    recipe_id=recipe.id,
                    week_number=1,
                    status="not_started",
                    feedback=None,
                    completed_at=None,
                )
            db.add(progress)
        db.commit()
        print(f"  ✅ Seeded progress records for recipes: {recipe_ids}")
        print(f"  ✅ Generated week 1 plan for Test user (mocked)")

        # Display summary
        print(f"\n📊 Database seeding complete! Summary:")
        print(f"  - Users: {db.query(User).count()}")
        print(f"  - Recipes: {db.query(Recipe).count()}")
        print(f"  - Weekly Plans: {db.query(WeeklyPlan).count()}")
        print(f"  - Progress Entries: {db.query(UserRecipeProgress).count()}")

        # Show Test user data
        print(f"\n� Test User:")
        print(
            f"  - {test_user.email} (ID: {test_user.id}) - {test_user.cuisine} cuisine, {test_user.skill_level} level"
        )

        return True

    except Exception as e:
        print(f"❌ Error seeding database: {e}")
        db.rollback()
        return False
    finally:
        await plan_service.close()
        db.close()


def main():
    """Main function to handle command line arguments"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed ChefPath database with sample data"
    )
    parser.add_argument(
        "--clear", action="store_true", help="Clear database before seeding"
    )
    args = parser.parse_args()

    # Run the async seeding function
    success = asyncio.run(seed_database(clear_first=args.clear))

    if success:
        print("\n🎉 Database seeding completed successfully!")
        print("\n💡 Next steps:")
        print("  1. Start the API server: python main.py")
        print("  2. Test endpoints with the sample users")
        print("  3. Use scripts/view_database.py to inspect the data")
    else:
        print("\n❌ Database seeding failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
