"""
FastAPI app for the Sostojka entity-linking explorer.

Thin routing layer: configuration lives in ``config.py``, response models in
``schemas.py``, and the linking logic in the ``agent`` package. Serves the
vanilla-JS frontend and exposes the REST API.
"""

import json
from collections import Counter

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agent import EntityLinkingAgent
from backend.config import (
    DATA_PATH,
    FRONTEND_DIR,
    OPENAI_API_KEY,
    USDA_CSV_PATH,
)
from backend.schemas import (
    ApiKeyIn,
    IngredientInfo,
    LinkedIngredientOut,
    LinkedRecipeOut,
    RecipeDetail,
    RecipeSummary,
    TraceOut,
    recipe_to_detail,
    trace_to_out,
)

# Paths that belong to the API / assets and must NOT be served the SPA shell.
# All data endpoints live under /api, so only these first path segments are
# server-owned; every other path returns the SPA shell (English clean URLs).
RESERVED_PREFIXES = {"api", "static", "docs", "redoc", "openapi.json"}

# ---------------------------------------------------------------------------
# App & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sostojka API",
    description="Agentic entity linking for Macedonian recipes → USDA FoodData Central",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the static frontend assets at /static (scripts, styles).
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ---------------------------------------------------------------------------
# Startup: load agent & recipes once
# ---------------------------------------------------------------------------

agent: EntityLinkingAgent | None = None
recipes: list[dict] = []


@app.on_event("startup")
async def startup():
    global agent, recipes
    print("Loading dataset …")
    with open(DATA_PATH, encoding="utf-8") as f:
        recipes = json.load(f)
    print(f"  {len(recipes):,} recipes loaded")

    print("Initialising agent …")
    agent = EntityLinkingAgent(
        recipes_path=str(DATA_PATH),
        usda_csv_path=str(USDA_CSV_PATH),
        openai_api_key=OPENAI_API_KEY,
    )
    print("  Agent ready")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "recipes_loaded": len(recipes),
        "usda_entries": agent.usda_tool.size if agent else 0,
        "provider": agent.provider if agent else None,
        "engine": (agent.model if (agent and agent.client) else "rule-based (no API key)"),
    }


@app.post("/api/key")
async def set_api_key(body: ApiKeyIn):
    """Configure an OpenAI API key at runtime so the full LLM pipeline is enabled.

    The key is held only in memory for this server process — it is never logged
    or written to disk. Returns the active engine on success.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    if not agent.configure_openai(body.key, model=body.model or None):
        raise HTTPException(
            status_code=400,
            detail="The OpenAI API key was rejected. Check the key and try again.",
        )
    return {"status": "ok", "provider": agent.provider, "engine": agent.model}


@app.get("/api/smart")
async def smart(q: str = Query(..., description="Free-text query — ingredient or recipe")):
    """Classify the query so the UI can route it: 'ingredient' or 'recipe'."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    return {"query": q, "kind": agent.classify_query(q)}


@app.get("/api/recipes", response_model=list[RecipeSummary])
async def list_recipes(
    q: str = Query("", description="Free-text search in title or tags"),
    tag: str = Query("", description="Filter by exact category tag (case-insensitive)"),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
):
    """List / search recipes from the dataset.

    ``q`` is a fuzzy substring search over title and tags; ``tag`` keeps only
    recipes that actually carry that exact category tag. Both can be combined.
    """
    filtered = recipes
    if q:
        ql = q.lower()
        filtered = [
            r for r in filtered
            if ql in r.get("title", "").lower()
            or any(ql in t.lower() for t in r.get("tags", []))
        ]
    if tag:
        tl = tag.lower()
        filtered = [
            r for r in filtered
            if any(t.lower() == tl for t in r.get("tags", []))
        ]

    page = filtered[offset: offset + limit]
    return [
        RecipeSummary(
            index=recipes.index(r),
            title=r.get("title", ""),
            tags=r.get("tags", []),
            ingredient_count=len(r.get("ingredients", [])),
            source=r.get("source", ""),
            image=r.get("image") or "",
        )
        for r in page
    ]


@app.get("/api/recipes/{index}", response_model=RecipeDetail)
async def get_recipe(index: int):
    """Get a single recipe by its index in the dataset."""
    if index < 0 or index >= len(recipes):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return recipe_to_detail(index, recipes[index])


@app.get("/api/link/ingredient", response_model=TraceOut)
async def link_ingredient(
    name: str = Query(..., description="Ingredient name (Macedonian or English)"),
):
    """Run the agentic entity linker on a single ingredient (full 5-stage trace)."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    return trace_to_out(agent.link(name))


@app.get("/api/link/recipe/{index}", response_model=LinkedRecipeOut)
async def link_recipe(index: int):
    """Run the agentic entity linker on every ingredient of a recipe."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    if index < 0 or index >= len(recipes):
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe = recipes[index]
    result = agent.link_recipe(recipe)

    linked = [
        LinkedIngredientOut(
            original=IngredientInfo(
                quantity=item["original"].get("quantity", ""),
                unit=item["original"].get("unit", ""),
                name=item["original"].get("name", ""),
            ),
            trace=trace_to_out(item["trace"]),
        )
        for item in result["linked_ingredients"]
    ]

    return LinkedRecipeOut(recipe=recipe_to_detail(index, recipe), linked_ingredients=linked)


@app.get("/api/stats")
async def stats():
    """Dataset statistics."""
    all_tags = [tag for r in recipes for tag in r.get("tags", [])]
    top_tags = Counter(all_tags).most_common(15)
    return {
        "total_recipes": len(recipes),
        "recipes_with_ingredients": sum(1 for r in recipes if r.get("ingredients")),
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
    }


# ---------------------------------------------------------------------------
# Frontend (SPA) — clean URLs without a #hash
#
# The root and any client-side route (/ingredient, /recipes, /recipe/{i},
# /documentation) all return index.html; the JS router renders the right page.
# Declared last so it never shadows the API routes above.
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path.split("/", 1)[0] in RESERVED_PREFIXES:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(FRONTEND_DIR / "index.html"))
