from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.database import create_tables
from app.routers import users, recipes, weekly_plans, feedback, auth, plan_agent
from app.agents.checkpoint_setup import initialize_postgres_saver
from app.core.rate_limit import limiter
from app.database import engine
from sqlalchemy import text
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()

    app.state.checkpoint_saver = initialize_postgres_saver()
    print("✅ Checkpoint saver (in-memory) is active and ready.")

    yield


app = FastAPI(title="ChefPath Backend", version="1.0.0", lifespan=lifespan)

# Configure rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Configure CORS ---
# Env vars:
#   CORS_ORIGINS        Comma-separated exact origins (e.g.
#                       "https://chefpath.vercel.app,http://localhost:3000")
#   CORS_ORIGIN_REGEX   Optional regex for dynamic origins like Vercel
#                       preview deployments (e.g.
#                       "^https://chefpath(-[\w-]+)?\.vercel\.app$")
#
# Browsers reject "Access-Control-Allow-Origin: *" combined with
# "Access-Control-Allow-Credentials: true", so we never emit "*" here.
# In dev we fall back to localhost defaults; in prod CORS_ORIGINS must be set.
_default_dev_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_raw_origins = os.getenv("CORS_ORIGINS", "").strip()
if _raw_origins in ("", "*"):
    allow_origins = list(_default_dev_origins)
    if _raw_origins == "*":
        print(
            "[CORS] WARNING: CORS_ORIGINS='*' is not allowed with credentials. "
            "Falling back to localhost dev origins. Set CORS_ORIGINS to an "
            "explicit comma-separated list (e.g. https://chefpath.vercel.app)."
        )
else:
    allow_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

allow_origin_regex = os.getenv("CORS_ORIGIN_REGEX") or None

print(f"[CORS] Configured origins={allow_origins} regex={allow_origin_regex!r}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,  # Cache preflight requests for 1 hour
)


# Include routers
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(recipes.router, prefix="/api", tags=["recipes"])
app.include_router(weekly_plans.router, prefix="/api", tags=["weekly-plans"])
app.include_router(feedback.router, prefix="/api", tags=["feedback"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(plan_agent.router, prefix="/plan", tags=["plan generation"])


@app.get("/")
async def root():
    return {"message": "ChefPath Backend API is running!"}


@app.get("/health")
def health_check():
    """
    Health check endpoint for Railway.

    Railway uses this to verify the service is responsive.
    If this endpoint fails, Railway will restart the service.
    """
    try:
        # Verify database connection is alive
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        return {"status": "ok", "service": "chefpath-backend", "database": "connected"}
    except Exception as e:
        return {
            "status": "degraded",
            "service": "chefpath-backend",
            "database": "disconnected",
            "error": str(e),
        }
