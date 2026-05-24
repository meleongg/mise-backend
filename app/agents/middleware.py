"""
Middleware for LangGraph Agent

Implements message management and optimization patterns:
1. Message trimming to prevent context window overflow
2. Context injection for efficient token usage
"""

from typing import Dict, Any, List
from langchain_core.messages import ToolMessage, AIMessage
from langchain_core.messages import SystemMessage, AnyMessage
from app.agents.runtime_context import get_runtime_context


def trim_messages(state: Dict[str, Any], max_messages: int = 10) -> Dict[str, Any]:
    """
    Trim message history to prevent context window overflow.

    Strategy:
    - Always keep the first message (initial user intent)
    - Keep the last N messages for recent context
    - IMPORTANT: Ensure ToolMessage always has its preceding AIMessage with tool_calls
    - This prevents token bloat in multi-turn conversations

    Args:
        state: Current agent state
        max_messages: Maximum number of messages to keep (default: 10)

    Returns:
        Updated state with trimmed messages
    """
    messages = state.get("messages", [])

    if len(messages) <= max_messages:
        return state  # No trimming needed

    # Keep first message (initial intent) + last (max_messages - 1)
    trimmed_messages = [messages[0]] + messages[-(max_messages - 1) :]

    # Ensure no orphaned ToolMessages
    # If the first message in the tail is a ToolMessage, we need to include
    # the preceding AIMessage with tool_calls to maintain the chain
    if len(trimmed_messages) > 1 and isinstance(trimmed_messages[1], ToolMessage):
        # Find the preceding AIMessage with tool_calls
        start_index = -(max_messages - 1)  # Where we started taking from

        # Walk backwards from start_index to find the AIMessage with tool_calls
        for i in range(len(messages) + start_index - 1, -1, -1):
            msg = messages[i]
            if (
                isinstance(msg, AIMessage)
                and hasattr(msg, "tool_calls")
                and msg.tool_calls
            ):
                # Found the AIMessage with tool_calls, include it
                # Insert it right after the first message
                trimmed_messages.insert(1, msg)
                print(
                    f"[MessageTrimming] Added preceding AIMessage with tool_calls to prevent chain break"
                )
                break

    print(
        f"[MessageTrimming] Trimmed {len(messages)} → {len(trimmed_messages)} messages"
    )

    return {**state, "messages": trimmed_messages}


def create_context_message(state: Dict[str, Any]) -> SystemMessage:
    """
    Create a lightweight system message with runtime context.
    This is injected once rather than embedded in every message.

    Args:
        state: Current agent state

    Returns:
        SystemMessage with context information
    """

    # Get user preferences from runtime context
    try:
        context = get_runtime_context()
        cuisine = context.cuisine or "not specified"
        dietary_restrictions = (
            ", ".join(context.dietary_restrictions)
            if context.dietary_restrictions
            else "none"
        )
        allergens = ", ".join(context.allergens) if context.allergens else "none"
        skill_level = context.skill_level or "intermediate"
    except:
        # Fallback if runtime context not available
        cuisine = "not specified"
        dietary_restrictions = "none"
        allergens = "none"
        skill_level = "intermediate"

    user_id = state.get("user_id", "unknown")
    user_goal = state.get("user_goal", "general")
    frequency = state.get("frequency", 3)
    exclude_ids = state.get("exclude_ids", [])

    context_msg = SystemMessage(content=f"""CURRENT CONTEXT:
- user_id: {user_id}
- user_goal: {user_goal}
- frequency: {frequency} meals
- exclude_ids: {len(exclude_ids)} recipes excluded (cooldown + difficulty)

USER PREFERENCES (MUST RESPECT):
- Preferred Cuisine: {cuisine}
- Skill Level: {skill_level}
- Dietary Restrictions: {dietary_restrictions}
- Allergens to Avoid: {allergens}

IMPORTANT: When searching for recipes, you MUST prioritize the user's preferred cuisine ({cuisine}).
The search tool will automatically filter by these preferences, but ensure your search queries
align with the user's cuisine preference and skill level.

You have access to tools that automatically use this context.
DO NOT pass user_id or exclude_ids explicitly - they are injected automatically.""")

    return context_msg


def prepare_messages_for_llm(
    state: Dict[str, Any], system_prompt: str, trim: bool = True, max_messages: int = 10
) -> List[AnyMessage]:
    """
    Prepare messages for LLM invocation with optional trimming and context injection.

    Args:
        state: Current agent state
        system_prompt: System prompt to prepend
        trim: Whether to trim message history (default: True)
        max_messages: Maximum messages to keep if trimming (default: 10)

    Returns:
        List of messages ready for LLM invocation
    """
    # Apply trimming middleware if enabled
    if trim:
        state = trim_messages(state, max_messages=max_messages)

    # Build message list: system prompt + context + conversation history
    messages = [
        SystemMessage(content=system_prompt),
        create_context_message(state),
    ] + state["messages"]

    return messages
