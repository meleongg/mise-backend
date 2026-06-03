"""
Rate limiting configuration for ChefPath API.

Uses slowapi to protect AI endpoints from abuse.
Configured to work with Railway's proxy using X-Forwarded-For header.
"""

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

# Per-endpoint limits (override via env)
CHAT_RATE_LIMIT = os.getenv("AI_CHAT_RATE_LIMIT", "5/minute")
PLAN_GEN_RATE_LIMIT = os.getenv("AI_PLAN_GEN_RATE_LIMIT", "3/hour")
SWAP_RATE_LIMIT = os.getenv("AI_SWAP_RATE_LIMIT", "10/hour")


def get_real_ip(request: Request) -> str:
    """
    Extract the real client IP from request headers.

    Priority:
    1. X-Forwarded-For (first IP in chain) - for Railway/proxy deployments
    2. X-Real-IP - alternative proxy header
    3. request.client.host - direct connection fallback
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    return get_remote_address(request)


def get_user_id_rate_limit_key(request: Request) -> str:
    """Per-user bucket for authenticated plan routes (path includes user_id)."""
    user_id = request.path_params.get("user_id")
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_real_ip(request)}"


# Initialize the rate limiter with custom key function
limiter = Limiter(key_func=get_real_ip)
