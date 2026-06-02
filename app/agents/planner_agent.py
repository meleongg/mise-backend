import operator
import re
import os
from typing import TypedDict, Annotated, List, Literal
from dotenv import load_dotenv
from app.agents.runtime_context import get_runtime_state
from langchain_core.messages import AnyMessage, ToolMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langsmith import traceable
from app.agents.middleware import prepare_messages_for_llm
from app.services.adaptive_planner import (
    get_recipe_candidates,
    generate_and_save_new_recipe,
    finalize_recipe_selection,
)
from app.constants import GENERATIVE_MODEL
from app.errors.planner_agent import NoRecipesSelectedError

load_dotenv()

# Check if tracing is enabled
TRACING_ENABLED = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"


# --- Define the Graph State Schema ---
class PlanState(TypedDict):
    """
    Represents the state of the adaptive meal planning workflow.

    CONTEXT (passed in, read-only for tools via runtime):
    - user_id, user_goal, frequency, exclude_ids

    STATE (modified by agent/tools):
    - messages, candidate_recipes, next_action
    """

    # Conversation History (MUST use operator.add to append messages)
    messages: Annotated[List[AnyMessage], operator.add]

    # CONTEXT FIELDS (read-only, injected into runtime for tools)
    user_id: str
    user_goal: str
    frequency: int  # User's preferred number of meals per week
    exclude_ids: List[
        str
    ]  # Recipe ID strings to exclude (difficult + recently completed)

    # STATE FIELDS (modified by agent and tools)
    candidate_recipes: List[str]  # List of recipe ID strings found by Vector Search
    next_action: Literal["tool", "generate", "end"]  # Agent decision marker


# System prompt for the AI agent
SYSTEM_PROMPT = """You are Sodie, the expert adaptive meal planning assistant for Mise.

Your goal is to find or generate recipes for the user by calling tools.

IMPORTANT: You DON'T need to pass user_id, exclude_ids, or frequency to tools.
These are automatically injected from the runtime context.

WORKFLOW:
Step 1: Call get_recipe_candidates tool
- Only pass: intent_query (your search string describing desired recipes)
- The tool automatically knows the user, exclusions, and how many recipes are needed
- The tool will populate candidate_recipes state and tell you if there's a shortfall
- Create a search query based on user preferences (cuisine, difficulty, goals)

Step 2: Check candidate_recipes state
- After search, check how many recipes you found
- The tool tells you the shortfall automatically

- If len(candidate_recipes) >= frequency:
  ✅ You have enough recipes!
  - Call finalize_recipe_selection with the recipe IDs from candidate_recipes
  - Do NOT generate additional recipes

- If len(candidate_recipes) < frequency:
  ⚠️ Not enough recipes - need to generate more
  a) Calculate shortfall: frequency - len(candidate_recipes)
  b) Call generate_and_save_new_recipe ONCE for ONE recipe
     - Provide detailed recipe description (cuisine, difficulty, goal)
     - Example: "A beginner-friendly Mexican taco recipe focusing on building confidence"
     - The tool automatically adds the new recipe to candidate_recipes
  c) Check candidate_recipes again
  d) Repeat if still short
  e) Once you have enough, call finalize_recipe_selection

- If NO recipes found (len(candidate_recipes) == 0):
  a) Try one more search with a different query
  b) If still no results, generate all needed recipes one at a time
  c) Call finalize_recipe_selection when done

Step 3: Recipe Generation Guidelines
- Include: cuisine, difficulty, user_goal in your recipe_description
- Be specific and actionable
- Generate ONE recipe at a time
- Check candidate_recipes count after each generation

PLAN MODIFICATION MODE:
When modifying an existing plan:
1. candidate_recipes already contains the current plan's recipe IDs
2. Analyze which specific recipe(s) to change
3. Keep unchanged recipes, only replace what's requested
4. Use get_recipe_candidates or generate_and_save_new_recipe for replacements
5. Call finalize_recipe_selection with the updated list (mix of old + new IDs)

Example:
- Current: ["recipe-A-id", "recipe-B-id", "recipe-C-id"]
- User: "Change the second recipe to vegetarian"
- Search for vegetarian → "recipe-D-id"
- Updated: ["recipe-A-id", "recipe-D-id", "recipe-C-id"]

RECIPE SWAP MODE:
When handling a targeted recipe swap request:

1. THE REQUEST includes:
   - Recipe name and ID to replace
   - User's reason for the swap
   - User preferences in CURRENT CONTEXT

2. YOUR TASK:
   - Call get_recipe_candidates() with a search query including:
     * User's cuisine preference (from CURRENT CONTEXT)
     * The swap reason
     * User's skill level
     * Example: "Chinese sweet recipe for beginner"
   
3. REVIEW THE RESULTS:
   - The tool returns a formatted list with:
     * Recipe ID (UUID)
     * Recipe name (e.g., "Shrimp Chow Fun")
     * Difficulty level
     * Similarity score

4. PICK THE BEST RECIPE:
   - Choose the recipe with the highest similarity score (usually #1)
   - Or choose best fit to swap reason if scores are close

5. RESPOND WITH ONLY:
   - The UUID of that ONE recipe
   - Example: "8a9cf1e4-1e3f-49e7-babe-01941de08134"

6. THE BACKEND WILL:
   - Insert this recipe at correct position in meal plan
   - Save to database
   - Return complete updated plan

KEY: Your job is to evaluate and pick. The backend handles list manipulation.

RULES:
- In SWAP MODE: Just respond with the recipe UUID, don't call finalize_recipe_selection
- In normal PLAN MODE: ALWAYS call finalize_recipe_selection as final step
- Tools automatically access user context - don't pass it manually
- Never select recipes in exclude_ids; this list includes a 14-day cooldown (plan + swap)
- Only generate recipes when there's a genuine shortfall
- Generate ONE recipe at a time
- Keep responses concise
- If tool fails, check candidate_recipes before retrying

The system will handle everything else."""

# init AI agent with tools bound
tools = [get_recipe_candidates, generate_and_save_new_recipe, finalize_recipe_selection]
llm = ChatOpenAI(model=GENERATIVE_MODEL, temperature=0)
# LLM receives tool schemas
llm_with_tools = llm.bind_tools(tools)

# Create tool node for executing tools
tool_node = ToolNode(tools)


# main LLM agent reasoning node
@traceable(name="agent_reasoner_node")
def call_agent_reasoner(state: PlanState) -> PlanState:
    """
    The main reasoning node with message trimming middleware.
    Reduces token usage by limiting context window and using middleware for context injection.
    """
    print("\n[call_agent_reasoner] === ENTERED ===")
    print(
        f"[call_agent_reasoner] Current candidate_recipes: {state.get('candidate_recipes', [])}"
    )
    print(
        f"[call_agent_reasoner] Message count BEFORE trim: {len(state.get('messages', []))}"
    )

    try:
        # Use middleware to prepare messages (with trimming and context injection)
        messages = prepare_messages_for_llm(
            state, SYSTEM_PROMPT, trim=True, max_messages=10
        )

        print(
            f"[call_agent_reasoner] Message count AFTER trim: {len(messages) - 2}"
        )  # -2 for system prompt and context
        print(f"[call_agent_reasoner] Invoking agent...")

        response = llm_with_tools.invoke(messages)

        print(f"[call_agent_reasoner] Agent response type: {type(response)}")
        print(
            f"[call_agent_reasoner] Agent response content: {response.content[:200] if response.content else 'No content'}"
        )

        # response is an AIMessage directly, not a dict
        print(
            f"[call_agent_reasoner] Has tool_calls: {bool(getattr(response, 'tool_calls', False))}"
        )

        if getattr(response, "tool_calls", False):
            # LLM is requesting a tool call, so route to tool node
            print(f"[call_agent_reasoner] Tool calls: {response.tool_calls}")
            return {"messages": [response], "next_action": "tool"}
        else:
            # LLM gave a text response
            print(f"[call_agent_reasoner] No tool calls, ending cycle")
            return {"messages": [response], "next_action": "end"}

    except Exception as e:
        print(f"❌ EXCEPTION in call_agent_reasoner: {type(e).__name__}: {e}")
        error_message = f"AI Error: Unable to communicate with the planning engine due to a network issue. Please try again. ({type(e).__name__})"
        return {"messages": [AIMessage(content=error_message)], "next_action": "end"}


# tool execution node
@traceable(name="execute_tool_node")
def execute_tool(state: PlanState) -> PlanState:
    """
    Execute the tool called by the LLM and sync runtime state back to graph state.
    With runtime context, tools update state directly - we just need to sync it back.
    """
    print("\n[execute_tool] === ENTERED ===")
    print(
        f"[execute_tool] Current candidate_recipes BEFORE tool: {state.get('candidate_recipes', [])}"
    )

    # The last message contains the tool call request from the LLM
    tool_call = state["messages"][-1].tool_calls[0]
    tool_name = tool_call.get("name")
    print(f"[execute_tool] Tool name: {tool_name}")
    print(f"[execute_tool] Tool args: {tool_call.get('args', {})}")

    try:
        # Use ToolNode to execute the tool
        result = tool_node.invoke(state)
        print(f"[execute_tool] Tool result type: {type(result)}")
        print(f"[execute_tool] Tool result: {result}")

        # Sync runtime state back to graph state
        runtime_state = get_runtime_state()
        updated_candidates = runtime_state.candidate_recipes

        print(f"[execute_tool] ✓ Syncing runtime state → graph state")
        print(f"[execute_tool]   candidate_recipes: {len(updated_candidates)} recipes")

        # Handle finalize_recipe_selection - marks the end of the workflow
        if tool_name == "finalize_recipe_selection":
            recipe_ids = tool_call.get("args", {}).get("recipe_ids", [])
            print(f"[execute_tool] Finalizing selection with recipe_ids: {recipe_ids}")
            return {
                "messages": result["messages"],
                "candidate_recipes": recipe_ids,
                "next_action": "end",
            }

        # For other tools, sync runtime state to graph state
        return {
            "messages": result["messages"],
            "candidate_recipes": updated_candidates,
        }

    except Exception as e:
        error_content = f"Tool Execution Failed: The database retrieval encountered an error (e.g., connection timeout or bad query syntax). Error Type: {type(e).__name__}: {str(e)}"
        return {
            "messages": [
                ToolMessage(content=error_content, tool_call_id=tool_call["id"])
            ]
        }


# output node
@traceable(name="finalize_plan_node")
def finalize_plan_output(state: PlanState) -> PlanState:
    """
    Processes the final tool output (which should be the submitted plan)
    and updates the PlanState with the final list of UUIDs.
    """
    print("\n[finalize_plan_output] === ENTERED ===")
    print(f"[finalize_plan_output] Full state keys: {state.keys()}")
    print(f"[finalize_plan_output] State: {state}")

    # assume recipes are provided (as a result of previous graph nodes)
    final_selections = state.get("candidate_recipes", [])
    print(f"[finalize_plan_output] Final selections: {final_selections}")
    print(f"[finalize_plan_output] Final selections type: {type(final_selections)}")
    print(f"[finalize_plan_output] Final selections length: {len(final_selections)}")

    if not final_selections:
        print("[finalize_plan_output] ❌ ERROR: No candidate recipes found!")
        # Check if agent provided an explanation in the last message
        last_message = state["messages"][-1]
        if hasattr(last_message, "content") and last_message.content:
            # Agent explained why no recipes were found - this is acceptable
            agent_explanation = last_message.content
            print(
                f"[finalize_plan_output] Agent explanation: {agent_explanation[:200]}"
            )
            raise NoRecipesSelectedError(
                f"No recipes found matching the criteria. Agent response: {agent_explanation}"
            )
        else:
            # No explanation - something went wrong
            raise NoRecipesSelectedError(
                "No candidate recipes were selected by the agent. Cannot finalize plan."
            )

    print(f"[finalize_plan_output] ✅ Returning {len(final_selections)} recipes")
    return {"candidate_recipes": final_selections}


# conditional router
def route_agent_action(state: PlanState) -> str:
    """Determines the next step based on the Agent's last message."""
    print("\n[route_agent_action] === ROUTING ===")
    last_message = state["messages"][-1]
    has_tool_calls = bool(
        last_message.tool_calls if hasattr(last_message, "tool_calls") else False
    )
    print(f"[route_agent_action] Last message has tool_calls: {has_tool_calls}")
    print(
        f"[route_agent_action] Current candidate_recipes: {state.get('candidate_recipes', [])}"
    )

    if has_tool_calls:
        # If the LLM requested a tool, go execute it
        print("[route_agent_action] → Routing to 'tool'")
        return "tool"
    else:
        # If the LLM responded with text (no tool calls), check if we have recipes
        candidate_recipes = state.get("candidate_recipes", [])
        if candidate_recipes:
            # Agent found recipes and finalized them, go to finalizer
            print("[route_agent_action] → Routing to 'end' (finalizer)")
            return "end"
        else:
            # Agent couldn't find recipes and gave up gracefully
            # This is actually a valid end state - the agent explained the situation
            print(
                "[route_agent_action] → Routing to 'end' (no recipes, agent gave explanation)"
            )
            # We'll raise a clear error in the finalizer
            return "end"


# --- Build the Graph ---
planner_builder = StateGraph(PlanState)

planner_builder.add_node("agent", call_agent_reasoner)
planner_builder.add_node("tool", execute_tool)
planner_builder.add_node("finalizer", finalize_plan_output)

planner_builder.set_entry_point("agent")

# Define Conditional Edge: After the agent reasons, does it need a tool or is it done?
planner_builder.add_conditional_edges(
    "agent", route_agent_action, {"tool": "tool", "end": "finalizer"}
)

# Define Loop Edge: After the tool runs, go back to the agent to reason about the tool output
planner_builder.add_edge("tool", "agent")

planner_builder.add_edge("finalizer", END)

# Don't compile yet - we need the checkpointer first
# Export the builder so endpoints can compile with their checkpointer
PlannerGraphBuilder = planner_builder


def get_agent_with_checkpointer(checkpointer):
    """Compile the agent graph with the provided checkpointer."""
    return planner_builder.compile(checkpointer=checkpointer)
