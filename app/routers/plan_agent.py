import json
import logging
import re
import uuid
import os
from dotenv import load_dotenv
from app.constants import GENERATIVE_MODEL, MAX_SWAPS_PER_WEEK
from typing import Annotated, List, Dict, Any
from fastapi import APIRouter, Depends, Body, HTTPException, status, Request
from app.agents.runtime_context import (
    set_runtime_context,
    clear_runtime_context,
    PlannerContext,
    PlannerRuntimeState,
)
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from app.database import get_db
from app.models import User, WeeklyPlan, UserRecipeProgress, Recipe
from app.services.weekly_plan import (
    WeeklyPlanService,
    create_recipe_schedule,
    parse_recipe_schedule,
    swap_recipe_in_schedule,
    validate_recipe_can_be_swapped,
    cleanup_swapped_recipe_progress,
)
from app.agents.planner_agent import get_agent_with_checkpointer, PlanState
from app.schemas import (
    WeeklyPlanResponse,
    PlanGenerationInput,
    GeneralChatInput,
    AdaptiveChatResponse,
    SwapRecipeRequest,
    SwapRecipeResponse,
    RecipeResponse,
)
from app.services.intent_classifier import classify_message_intent
from app.services.sodie_chat_context import build_sodie_chat_context
from app.utils.uuid_helpers import uuids_to_strs, strs_to_uuids
from app.utils.prompt_helpers import get_goal_description, get_skill_description
from app.utils.auth import get_current_user, require_same_user
from app.core.rate_limit import (
    CHAT_RATE_LIMIT,
    PLAN_GEN_RATE_LIMIT,
    SWAP_RATE_LIMIT,
    get_user_id_rate_limit_key,
    limiter,
)
from app.services.content_moderation import ensure_user_text_allowed
from app.services.sodie_llm import build_coach_prompt, invoke_chat_model

# Load environment variables
load_dotenv()

# Check if tracing is enabled
TRACING_ENABLED = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"

router = APIRouter()
logger = logging.getLogger(__name__)


def get_weekly_plan_service() -> WeeklyPlanService:
    return WeeklyPlanService()


def _get_sodie_coach_response(
    user_message: str, context: str, *, mode: str = "general_knowledge"
) -> str:
    """Context-aware Sodie response for coach and analytics chat modes."""
    llm = ChatOpenAI(model=GENERATIVE_MODEL, temperature=0.5)
    prompt = build_coach_prompt(user_message, context, mode=mode)
    return invoke_chat_model(llm, prompt)


@router.post("/general/{user_id}", response_model=Dict[str, str])
@limiter.limit(CHAT_RATE_LIMIT)
@limiter.limit(CHAT_RATE_LIMIT, key_func=get_user_id_rate_limit_key)
async def casual_chat_endpoint(
    request: Request,
    user_id: uuid.UUID,
    chat_input: GeneralChatInput = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Handles general knowledge questions using a low-cost LLM (Stateless).
    Bypasses the expensive LangGraph Agent and RAG tools.
    """
    require_same_user(current_user, user_id)
    try:
        ensure_user_text_allowed(chat_input.user_message)
        context = build_sodie_chat_context(db, current_user, chat_input.week_number)
        response_content = _get_sodie_coach_response(
            chat_input.user_message, context, mode="general_knowledge"
        )
        return {"response": response_content}

    except HTTPException:
        raise
    except Exception:
        logger.exception("General chat failed user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error communicating with the general knowledge AI.",
        )


@router.post("/adaptive_chat/{user_id}", response_model=AdaptiveChatResponse)
@limiter.limit(CHAT_RATE_LIMIT)
@limiter.limit(CHAT_RATE_LIMIT, key_func=get_user_id_rate_limit_key)
async def adaptive_chat_endpoint(
    request: Request,
    user_id: uuid.UUID,
    chat_input: GeneralChatInput = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Adaptive chat endpoint that classifies intent and routes accordingly.

    Routes:
    - general_knowledge → Context-aware coach Q&A
    - analytics → Progress/stats from user context

    Note: Recipe modifications are handled via dedicated /swap-recipe endpoint.

    Returns:
        AdaptiveChatResponse with response text and intent
    """
    require_same_user(current_user, user_id)
    try:
        ensure_user_text_allowed(chat_input.user_message)
        context = build_sodie_chat_context(db, current_user, chat_input.week_number)

        logger.info(
            "AdaptiveChat classify user_id=%s week=%s msg_len=%s",
            user_id,
            chat_input.week_number,
            len(chat_input.user_message),
        )
        intent = classify_message_intent(chat_input.user_message)
        logger.info("AdaptiveChat intent=%s user_id=%s", intent, user_id)

        mode = "analytics" if intent == "analytics" else "general_knowledge"
        response_content = _get_sodie_coach_response(
            chat_input.user_message, context, mode=mode
        )
        return AdaptiveChatResponse(
            response=response_content,
            intent=intent if intent == "analytics" else "general_knowledge",
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("AdaptiveChat failed user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error processing chat message.",
        )


@router.post("/swap-recipe/{user_id}")
@limiter.limit(SWAP_RATE_LIMIT, key_func=get_user_id_rate_limit_key)
async def swap_recipe_endpoint(
    request: Request,
    user_id: uuid.UUID,
    swap_request: SwapRecipeRequest = Body(...),
    plan_service: Annotated[WeeklyPlanService, Depends(get_weekly_plan_service)] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Swap a single recipe in the user's weekly plan with an AI-powered replacement.

    Args:
        user_id: The user's UUID
        swap_request: Contains recipe_id_to_replace, optional week_number, and swap_context

    Returns:
        SwapRecipeResponse with old and new recipe details

    Raises:
        400: Recipe is already completed or not found in plan
        404: User or plan not found
        500: Swap operation failed
    """
    require_same_user(current_user, user_id)
    ensure_user_text_allowed(swap_request.swap_context)
    print(f"\n[SwapRecipe] Starting swap for user: {user_id}")
    print(f"[SwapRecipe] Recipe to replace: {swap_request.recipe_id_to_replace}")
    print(f"[SwapRecipe] Swap context: {swap_request.swap_context}")

    try:
        # 1. Validate user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # 2. Get target week (provided or most recent)
        if swap_request.week_number is not None:
            target_plan = (
                db.query(WeeklyPlan)
                .filter(
                    WeeklyPlan.user_id == user_id,
                    WeeklyPlan.week_number == swap_request.week_number,
                )
                .first()
            )
        else:
            target_plan = (
                db.query(WeeklyPlan)
                .filter(WeeklyPlan.user_id == user_id)
                .order_by(WeeklyPlan.week_number.desc())
                .first()
            )

        if not target_plan:
            raise HTTPException(status_code=404, detail="Weekly plan not found")

        print(f"[SwapRecipe] Target week: {target_plan.week_number}")

        # 2a. Check swap limit
        current_swap_count = getattr(target_plan, "swap_count", 0)
        if current_swap_count >= MAX_SWAPS_PER_WEEK:
            raise HTTPException(
                status_code=400,
                detail=f"Swap limit reached for this week ({MAX_SWAPS_PER_WEEK} swaps max). Come back next week!",
            )

        print(f"[SwapRecipe] Swaps used: {current_swap_count}/{MAX_SWAPS_PER_WEEK}")

        # 3. Load current plan and parse recipe_schedule
        try:
            current_recipe_ids_str = parse_recipe_schedule(target_plan.recipe_schedule)
            print(
                f"[SwapRecipe] Current plan has {len(current_recipe_ids_str)} recipes"
            )
        except Exception as e:
            print(f"[SwapRecipe] Error parsing recipe schedule: {e}")
            raise HTTPException(status_code=400, detail="Invalid plan data")

        # 4. Validate recipe_id_to_replace is in the plan
        recipe_id_str = str(swap_request.recipe_id_to_replace)
        if recipe_id_str not in current_recipe_ids_str:
            raise HTTPException(
                status_code=404,
                detail="Recipe not found in weekly plan",
            )

        # Get old recipe details before swap
        old_recipe = (
            db.query(Recipe)
            .filter(Recipe.id == swap_request.recipe_id_to_replace)
            .first()
        )
        if not old_recipe:
            raise HTTPException(status_code=404, detail="Recipe not found in database")

        print(f"[SwapRecipe] Swapping recipe: {old_recipe.name}")

        # 5. Validate recipe is NOT completed
        validate_recipe_can_be_swapped(
            user_id=user_id,
            recipe_id=swap_request.recipe_id_to_replace,
            week_number=target_plan.week_number,
            db=db,
        )

        # 6. Build comprehensive exclusion list
        # Include: user's poor-rated recipes + recipe being swapped + all other recipes in current plan
        exclusion_ids: List[uuid.UUID] = plan_service.get_user_exclusion_ids(
            user, db, current_week=target_plan.week_number
        )

        # Add ALL current plan recipes to exclusions (except the one being replaced)
        for current_id in current_recipe_ids_str:
            recipe_uuid = uuid.UUID(current_id)
            if (
                recipe_uuid != swap_request.recipe_id_to_replace
                and recipe_uuid not in exclusion_ids
            ):
                exclusion_ids.append(recipe_uuid)
                print(
                    f"[SwapRecipe] Added current plan recipe {current_id} to exclusions"
                )

        # Add the recipe being swapped to exclusions
        if swap_request.recipe_id_to_replace not in exclusion_ids:
            exclusion_ids.append(swap_request.recipe_id_to_replace)
            print(
                f"[SwapRecipe] Added recipe to swap {swap_request.recipe_id_to_replace} to exclusions"
            )

        print(f"[SwapRecipe] Total exclusions: {len(exclusion_ids)} recipes")
        exclude_ids_str = uuids_to_strs(exclusion_ids)

        # 7. Initialize runtime context with full user preferences
        runtime_context = PlannerContext(
            user_id=user.id,
            user_goal=user.user_goal,
            frequency=user.frequency,
            exclude_ids=exclusion_ids,
            skill_level=getattr(user, "skill_level", None),
            cuisine=getattr(user, "cuisine", None),
            dietary_restrictions=json.loads(
                getattr(user, "dietary_restrictions", "[]") or "[]"
            ),
            allergens=json.loads(getattr(user, "allergens", "[]") or "[]"),
            preferred_portion_size=getattr(user, "preferred_portion_size", None),
            max_prep_time_minutes=getattr(user, "max_prep_time_minutes", None),
            max_cook_time_minutes=getattr(user, "max_cook_time_minutes", None),
        )

        runtime_state = PlannerRuntimeState(
            candidate_recipes=current_recipe_ids_str.copy(),
            search_attempts=0,
            generation_attempts=0,
            is_swap_mode=True,  # Enable swap mode behavior
        )

        set_runtime_context(runtime_context, runtime_state)

        print("=" * 80)
        print("[SwapRecipe: RUNTIME CONTEXT INITIALIZED]")
        print(f"  user_id: {runtime_context.user_id}")
        print(f"  frequency: {runtime_context.frequency}")
        print(f"  cuisine: {runtime_context.cuisine}")
        print(f"  dietary_restrictions: {runtime_context.dietary_restrictions}")
        print(f"  allergens: {runtime_context.allergens}")
        print(f"  current recipes: {len(runtime_state.candidate_recipes)}")
        print("=" * 80)

        # 8. Build focused agent message - agent just picks ONE recipe ID
        swap_message = f"""RECIPE SWAP REQUEST:

Recipe to replace: {old_recipe.name} (ID: {recipe_id_str})
User's reason: {swap_request.swap_context}

YOUR JOB:
1. Search for ONE replacement recipe using get_recipe_candidates
2. Review the search results with recipe names, difficulty, and relevance scores
3. Pick the BEST recipe ID from the results (consider: relevance score, cuisine, difficulty)
4. Respond with ONLY that recipe ID (nothing else)

Example response: "8a9cf1e4-1e3f-49e7-babe-01941de08134"

The backend will handle inserting it into the meal plan."""

        # 9. Invoke LangGraph agent
        checkpointer = request.app.state.checkpoint_saver
        agent = get_agent_with_checkpointer(checkpointer)
        thread_id_str = f"{user_id}_swap_{target_plan.week_number}"

        agent_state: PlanState = {
            "messages": [HumanMessage(content=swap_message)],
            "user_id": str(user_id),
            "user_goal": user.user_goal,
            "candidate_recipes": current_recipe_ids_str,
            "frequency": user.frequency,
            "exclude_ids": exclude_ids_str,
        }

        print(f"[SwapRecipe] Invoking agent for swap...")

        with tracing_context(enabled=TRACING_ENABLED):
            updated_state: PlanState = agent.invoke(
                agent_state,
                config={
                    "configurable": {"thread_id": thread_id_str},
                    "recursion_limit": 25,
                },
            )

        # 10. Extract the new recipe ID from agent's response
        last_message = (
            updated_state.get("messages", [])[-1]
            if updated_state.get("messages")
            else None
        )
        if not last_message or not hasattr(last_message, "content"):
            raise ValueError("Agent did not provide a response")

        agent_response = last_message.content.strip()
        print(f"[SwapRecipe] Agent response: {agent_response}")

        # Try to extract UUID from response
        uuid_pattern = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        matches = re.findall(uuid_pattern, agent_response, re.IGNORECASE)

        if not matches:
            if len(runtime_state.swap_candidates) > 0:
                new_recipe_id_str = runtime_state.swap_candidates[0]
                print(
                    f"[SwapRecipe] Could not parse UUID, using first candidate: {new_recipe_id_str}"
                )
            else:
                raise ValueError(
                    f"Could not extract recipe ID from agent response: {agent_response}"
                )
        else:
            new_recipe_id_str = matches[0]
            print(f"[SwapRecipe] Extracted recipe ID: {new_recipe_id_str}")

        # 11. Backend performs deterministic swap
        try:
            # Use helper function to swap recipe in schedule while maintaining order
            updated_schedule_json = swap_recipe_in_schedule(
                target_plan.recipe_schedule, recipe_id_str, new_recipe_id_str
            )
            print(f"[SwapRecipe] Swapped {recipe_id_str} → {new_recipe_id_str}")
            print(f"[SwapRecipe] Updated schedule: {updated_schedule_json}")
        except ValueError:
            raise ValueError(f"Recipe {recipe_id_str} not found in current plan")

        # Get new recipe details
        new_recipe = (
            db.query(Recipe).filter(Recipe.id == uuid.UUID(new_recipe_id_str)).first()
        )
        if not new_recipe:
            raise HTTPException(
                status_code=500, detail="New recipe not found in database"
            )

        # 12. Update WeeklyPlan.recipe_schedule in database
        try:
            target_plan.recipe_schedule = updated_schedule_json

            # Record swap suggestions for cooldown tracking
            plan_service.record_recipe_suggestions(
                user_id=user.id,
                week_number=target_plan.week_number,
                recipe_ids=[swap_request.recipe_id_to_replace],
                source="swap_out",
                db=db,
            )
            plan_service.record_recipe_suggestions(
                user_id=user.id,
                week_number=target_plan.week_number,
                recipe_ids=[uuid.UUID(new_recipe_id_str)],
                source="swap_in",
                db=db,
            )

            # Increment swap count for this week
            target_plan.swap_count = current_swap_count + 1
            print(f"[SwapRecipe] Incremented swap_count to {target_plan.swap_count}")

            db.commit()
            db.refresh(target_plan)
            print(f"[SwapRecipe] ✅ Database updated successfully")
            print(f"[SwapRecipe] Saved to Supabase: {updated_schedule_json}")
        except Exception as e:
            db.rollback()
            print(f"[SwapRecipe] ❌ Database update failed: {e}")
            raise HTTPException(
                status_code=500, detail=f"Failed to save swap to database: {str(e)}"
            )

        print(f"[SwapRecipe] ✅ Swap completed: {old_recipe.name} → {new_recipe.name}")

        # 13. Delete old UserRecipeProgress entry
        cleanup_swapped_recipe_progress(
            user_id=user_id,
            old_recipe_id=swap_request.recipe_id_to_replace,
            week_number=target_plan.week_number,
            db=db,
        )
        db.commit()

        # 14. Return both old and new recipe details
        return SwapRecipeResponse(
            success=True,
            old_recipe=RecipeResponse.model_validate(old_recipe),
            new_recipe=RecipeResponse.model_validate(new_recipe),
            message=f"Successfully swapped {old_recipe.name} with {new_recipe.name}",
        )

    except HTTPException:
        raise
    except ValueError as e:
        print(f"[SwapRecipe] ValueError: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[SwapRecipe] Exception: {e}")
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Recipe swap failed: {str(e)}",
        )
    finally:
        clear_runtime_context()
        print("[SwapRecipe: RUNTIME CONTEXT CLEARED]")


@router.post("/generate/{user_id}", response_model=WeeklyPlanResponse)
@limiter.limit(PLAN_GEN_RATE_LIMIT, key_func=get_user_id_rate_limit_key)
async def generate_user_plan_endpoint(
    request: Request,
    user_id: uuid.UUID,
    input: PlanGenerationInput = Body(...),
    plan_service: Annotated[WeeklyPlanService, Depends(get_weekly_plan_service)] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers the LangGraph Adaptive Agent to generate a new weekly plan
    based on user history and goals.

    Uses runtime context for efficient token management.
    """
    require_same_user(current_user, user_id)
    ensure_user_text_allowed(input.initial_intent)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Always generate/regenerate week 1 for initial plan generation
    week_number = 1

    exclusion_ids: List[uuid.UUID] = plan_service.get_user_exclusion_ids(
        user, db, current_week=week_number
    )

    exclude_ids_str = uuids_to_strs(exclusion_ids)

    # Initialize runtime context (NOT passed through LLM!)
    runtime_context = PlannerContext(
        user_id=user.id,
        user_goal=user.user_goal,
        frequency=user.frequency,
        exclude_ids=exclusion_ids,
        skill_level=getattr(user, "skill_level", None),
        cuisine=getattr(user, "cuisine", None),
        dietary_restrictions=json.loads(
            getattr(user, "dietary_restrictions", "[]") or "[]"
        ),
        allergens=json.loads(getattr(user, "allergens", "[]") or "[]"),
        preferred_portion_size=getattr(user, "preferred_portion_size", None),
        max_prep_time_minutes=getattr(user, "max_prep_time_minutes", None),
        max_cook_time_minutes=getattr(user, "max_cook_time_minutes", None),
    )

    runtime_state = PlannerRuntimeState(
        candidate_recipes=[],
        search_attempts=0,
        generation_attempts=0,
        is_swap_mode=False,
    )

    # Set runtime context for tools to access
    set_runtime_context(runtime_context, runtime_state)

    # Simplified initial state (context is in runtime, not LLM messages)
    initial_state: PlanState = {
        "messages": [HumanMessage(content=input.initial_intent)],
        "user_id": str(user.id),
        "user_goal": user.user_goal,
        "candidate_recipes": [],
        "frequency": user.frequency,
        "exclude_ids": exclude_ids_str,
    }

    thread_id_str = str(user.id)

    try:
        print("=" * 80)
        print("[PHASE 1: RUNTIME CONTEXT INITIALIZED]")
        print(f"  user_id: {runtime_context.user_id}")
        print(f"  frequency: {runtime_context.frequency}")
        print(f"  exclude_ids: {len(runtime_context.exclude_ids)} recipes")
        print("=" * 80)
        print("Initial state:", initial_state)
        print("Thread ID:", thread_id_str)

        # Get checkpointer from app.state and compile agent with it
        checkpointer = request.app.state.checkpoint_saver
        agent = get_agent_with_checkpointer(checkpointer)

        # the agent runs its entire cycle (retrieve, reason, generate)
        with tracing_context(enabled=TRACING_ENABLED):
            final_state: PlanState = agent.invoke(
                initial_state,
                config={
                    "configurable": {"thread_id": thread_id_str},
                    "recursion_limit": 25,
                },
            )

        print("Final state:", final_state)

        # Sync final state from runtime (runtime state is source of truth)
        final_recipe_ids_str: List[str] = (
            runtime_state.candidate_recipes or final_state.get("candidate_recipes", [])
        )

        print("=" * 80)
        print("[PHASE 1: RUNTIME STATE SUMMARY]")
        print(f"  Search attempts: {runtime_state.search_attempts}")
        print(f"  Generation attempts: {runtime_state.generation_attempts}")
        print(f"  Final recipes: {len(final_recipe_ids_str)}")
        print("=" * 80)
        print("Final recipe IDs (strings):", final_recipe_ids_str)

        if not final_recipe_ids_str:
            print("No recipe IDs returned by agent.")
            raise ValueError(
                "The Adaptive Planner Agent failed to select final recipe IDs."
            )

        # Convert string IDs back to UUIDs for database storage
        final_recipe_ids: List[uuid.UUID] = strs_to_uuids(final_recipe_ids_str)

        new_plan = await plan_service.generate_weekly_plan(
            user=user,
            week_number=week_number,
            recipe_ids_from_agent=final_recipe_ids,
            db=db,
        )
        print("New plan:", new_plan)

        return new_plan

    except ValueError as e:
        print("ValueError:", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        print("Exception:", e)
        print("DB type at error:", type(db))
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent Planning Critical Error: {e}",
        )
    finally:
        # Always clean up runtime context
        clear_runtime_context()
        print("[PHASE 1: RUNTIME CONTEXT CLEARED]")


@router.get("/can_generate_next_week/{user_id}", response_model=Dict[str, Any])
async def check_next_week_eligibility(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Check if user is eligible to generate next week's plan.

    Returns:
        can_generate: boolean indicating if user can generate next week
        current_week: the current week number
        next_week: the next week number to be generated
        completion_status: progress details (completed/total recipes)
        message: human-readable status message
    """
    require_same_user(current_user, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Get most recent plan
    last_plan = (
        db.query(WeeklyPlan)
        .filter(WeeklyPlan.user_id == user_id)
        .order_by(WeeklyPlan.week_number.desc())
        .first()
    )

    if not last_plan:
        return {
            "can_generate": False,
            "current_week": None,
            "next_week": None,
            "completion_status": "0/0",
            "message": "No plans exist yet. Generate your first week's plan.",
        }

    current_week = last_plan.week_number

    # Check progress for current week
    week_progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.week_number == current_week,
        )
        .all()
    )

    if not week_progress:
        return {
            "can_generate": False,
            "current_week": current_week,
            "next_week": current_week + 1,
            "completion_status": "0/0",
            "message": f"No progress entries found for week {current_week}.",
        }

    completed_count = sum(
        1 for p in week_progress if getattr(p, "status") == "completed"
    )
    total_count = len(week_progress)

    can_generate = completed_count == total_count

    return {
        "can_generate": can_generate,
        "current_week": current_week,
        "next_week": current_week + 1,
        "completion_status": f"{completed_count}/{total_count}",
        "message": (
            f"✅ Week {current_week} completed! Ready to generate week {current_week + 1}."
            if can_generate
            else f"Complete week {current_week} first. Progress: {completed_count}/{total_count} recipes."
        ),
    }


@router.post("/generate_next_week/{user_id}", response_model=WeeklyPlanResponse)
@limiter.limit(PLAN_GEN_RATE_LIMIT, key_func=get_user_id_rate_limit_key)
async def generate_next_week_plan(
    request: Request,
    user_id: uuid.UUID,
    plan_service: Annotated[WeeklyPlanService, Depends(get_weekly_plan_service)] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate the next week's meal plan.

    Requirements:
    - Current week must be completed (all recipes marked as completed)
    - Creates a new plan for week_number + 1
    - Uses AI agent to generate recipes based on user feedback and history
    """
    require_same_user(current_user, user_id)
    print(f"\n[GenerateNextWeek] Starting for user: {user_id}")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Get the most recent plan
    last_plan = (
        db.query(WeeklyPlan)
        .filter(WeeklyPlan.user_id == user_id)
        .order_by(WeeklyPlan.week_number.desc())
        .first()
    )

    if not last_plan:
        raise HTTPException(
            status_code=404,
            detail="No existing plan found. Please generate your first week's plan first.",
        )

    current_week = last_plan.week_number
    print(f"[GenerateNextWeek] Current week: {current_week}")

    # Check if current week is completed
    week_progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.week_number == current_week,
        )
        .all()
    )

    if not week_progress:
        raise HTTPException(
            status_code=400,
            detail="No progress entries found for current week. Cannot generate next week.",
        )

    completed_count = sum(
        1 for p in week_progress if getattr(p, "status") == "completed"
    )

    if completed_count < len(week_progress):
        raise HTTPException(
            status_code=400,
            detail=f"Current week not completed. {completed_count}/{len(week_progress)} recipes done. Complete all recipes before generating next week.",
        )

    print(
        f"[GenerateNextWeek] ✅ Week {current_week} completed. Generating week {current_week + 1}"
    )

    # Check if next week already exists
    next_week_number = current_week + 1
    existing_next_plan = (
        db.query(WeeklyPlan)
        .filter(
            WeeklyPlan.user_id == user_id,
            WeeklyPlan.week_number == next_week_number,
        )
        .first()
    )

    if existing_next_plan:
        print(
            f"[GenerateNextWeek] ⚠️ Week {next_week_number} already exists, will regenerate"
        )

    # Get exclusion IDs (difficult recipes + recipes from last 2 weeks)
    exclusion_ids: List[uuid.UUID] = plan_service.get_user_exclusion_ids(
        user, db, current_week=next_week_number
    )

    exclude_ids_str = uuids_to_strs(exclusion_ids)

    print(
        f"[GenerateNextWeek] Excluding {len(exclusion_ids)} recipes (difficult + recent)"
    )

    # Create initial state for agent
    # Use adaptive skill level based on user feedback
    adapted_skill = plan_service.adapt_skill_level(user, next_week_number, db)

    initial_intent = (
        f"Create a Week {next_week_number} weekly meal plan for a user who prefers {user.cuisine} cuisine, "
        f"who wants {user.frequency} meals per week, has this cooking skill level: {get_skill_description(adapted_skill)}, "
        f"and whose goal is: {get_goal_description(user.user_goal)}."
    )

    initial_state: PlanState = {
        "messages": [HumanMessage(content=initial_intent)],
        "user_id": str(user.id),
        "user_goal": user.user_goal,
        "candidate_recipes": [],
        "frequency": user.frequency,
        "exclude_ids": exclude_ids_str,
    }

    thread_id_str = str(user.id)

    try:
        print(f"[GenerateNextWeek] Initial state: {initial_state}")
        print(
            f"[GenerateNextWeek] Excluding {len(exclusion_ids)} recipe IDs: {exclude_ids_str}"
        )

        # Get checkpointer and compile agent
        checkpointer = request.app.state.checkpoint_saver
        agent = get_agent_with_checkpointer(checkpointer)

        # Initialize runtime context for the agent
        print("[PHASE 1: RUNTIME CONTEXT INITIALIZED FOR NEXT WEEK GENERATION]")
        runtime_context = PlannerContext(
            user_id=user.id,
            user_goal=user.user_goal,
            frequency=user.frequency,
            exclude_ids=exclusion_ids,
            skill_level=adapted_skill,
            cuisine=getattr(user, "cuisine", None),
            dietary_restrictions=json.loads(
                getattr(user, "dietary_restrictions", "[]") or "[]"
            ),
            allergens=json.loads(getattr(user, "allergens", "[]") or "[]"),
            preferred_portion_size=getattr(user, "preferred_portion_size", None),
            max_prep_time_minutes=getattr(user, "max_prep_time_minutes", None),
            max_cook_time_minutes=getattr(user, "max_cook_time_minutes", None),
        )
        runtime_state = PlannerRuntimeState(
            candidate_recipes=[],
            search_attempts=0,
            generation_attempts=0,
        )
        set_runtime_context(runtime_context, runtime_state)

        # Invoke agent to generate recipes
        with tracing_context(enabled=TRACING_ENABLED):
            final_state: PlanState = agent.invoke(
                initial_state,
                config={
                    "configurable": {"thread_id": thread_id_str},
                    "recursion_limit": 25,
                },
            )

        # Get results from runtime state (tools updated it directly)
        final_recipe_ids_str: List[str] = runtime_state.candidate_recipes
        print(f"[GenerateNextWeek] Agent returned {len(final_recipe_ids_str)} recipes")

        # Log runtime state statistics
        print(
            f"[PHASE 1: NEXT WEEK GENERATION RUNTIME STATE SUMMARY]\n"
            f"  candidate_recipes: {len(runtime_state.candidate_recipes)} recipes\n"
            f"  search_attempts: {runtime_state.search_attempts}\n"
            f"  generation_attempts: {runtime_state.generation_attempts}"
        )

        if not final_recipe_ids_str:
            raise ValueError("Agent failed to select recipes for next week.")

        final_recipe_ids: List[uuid.UUID] = strs_to_uuids(final_recipe_ids_str)

        # Generate the weekly plan (this will create progress entries too)
        new_plan = await plan_service.generate_weekly_plan(
            user=user,
            week_number=next_week_number,
            recipe_ids_from_agent=final_recipe_ids,
            db=db,
        )

        print(f"[GenerateNextWeek] ✅ Week {next_week_number} generated successfully")

        return new_plan

    except ValueError as e:
        print(f"[GenerateNextWeek] ValueError: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        print(f"[GenerateNextWeek] Exception: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Next week generation failed: {str(e)}",
        )
    finally:
        # Clean up runtime context
        clear_runtime_context()
        print("[PHASE 1: RUNTIME CONTEXT CLEARED]")
