<div align="center">
  <img src="frontend/assets/sostojka-logo.png" alt="Sostojka logo" width="112" height="112">
  <h1>Состојка · Sostojka</h1>
  <p>Локални рецепти. Поврзани состојки. / Local recipes. Connected ingredients.</p>
  <p><strong>Explore Macedonian recipes and link their ingredients to USDA food data,<br>with AI-assisted translation, recipe context, and clear match explanations.</strong></p>
  <p><a href="#setup--quick-start"><strong>Get started locally →</strong></a></p>
  <p><a href="#what-it-does">What it does</a> · <a href="#the-5-stage-pipeline">The pipeline</a> · <a href="#what-you-see-on-screen">The interface</a> · <a href="#llm-configuration">AI setup</a> · <a href="#data-sources">Data</a> · <a href="#setup--quick-start">How to run it</a> · <a href="#rest-api-endpoints">API</a></p>
</div>

**Sostojka** (состојка, Macedonian for “ingredient”) is a cross-lingual recipe explorer that maps Macedonian food ingredients to standardized entries in USDA FoodData Central. Built around the pipeline described in *Chapter 3.2 — LLM-Driven Agentic Entity Linking* of the thesis by Darko Gjorgjievski.

---

## Setup & Quick Start

### Prerequisites

- Python 3.9 or later
- An [OpenAI API key](https://platform.openai.com/api-keys) *(optional — the app works without one in rule-based fallback mode)*

### 1. Create and activate a virtual environment

```bash
python -m venv venv
```

**Windows:**
```bash
venv\Scripts\activate
```

**macOS/Linux:**
```bash
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure your API key

```bash
cp .env.example .env
```

Open `.env` and set your key:

```
OPENAI_API_KEY=sk-...
```

Skip this step if you want to run in rule-based fallback mode (no LLM calls).

### 4. Start the server

Run from the project root:

```bash
uvicorn backend.api:app --port 8000
```

### 5. Open the app

| URL | What you get |
|---|---|
| `http://localhost:8000` | Main UI |
| `http://localhost:8000/docs` | Interactive API docs (Swagger) |

You can also supply your OpenAI key at runtime without editing `.env`: click the **API key** button in the app header and paste it in. The key is held in memory only and never written to disk.

---

## What it does

The application takes a Macedonian recipe and answers one question for each ingredient: *which USDA food entity does this ingredient correspond to?*

Matching is non-trivial because:
- Ingredients are written in Macedonian (Cyrillic script)
- Generic terms like **павлака** ("cream") map to several distinct USDA entries depending on culinary context
- The USDA database uses standardized English descriptions that differ from how ingredients appear in recipes

The app solves this through a **5-stage agentic pipeline** and makes every reasoning step visible to the user, so you can see exactly how the agent arrived at each decision.

---

## The 5-stage pipeline

Every ingredient passes through the same pipeline. Each stage is shown on screen as a colour-coded card.

### Stage 1 — Analyze
Detects the language of the ingredient and translates it to English if needed.

- If an LLM is configured, it calls the model and asks for the BCP-47 language code and an English translation.
- Without an LLM, Cyrillic detection triggers a lookup in a built-in Macedonian-to-English dictionary (`MK_DICT`, ~100 entries). Multi-word and prefix matches are tried before falling back to token-level matching.
- Output: detected language (e.g. `mk`) and English translation (e.g. `sour cream`).

### Stage 2 — Decide Context
Decides whether the ingredient is ambiguous enough to require recipe context for disambiguation.

- With an LLM: the model reasons about whether the English translation maps to multiple distinct USDA categories (e.g. "cream" → heavy cream / sour cream / light cream / whipping cream).
- Without an LLM: checks the translation against a hardcoded set of generic terms (`oil`, `cheese`, `meat`, `cream`, `pepper`, etc.).
- Output: `needs_context = true/false` with a short reason.

### Stage 3 — Fetch Context *(conditional)*
Only runs when Stage 2 decided context is needed.

Calls the **`get_recipe_context`** tool, which searches the full Macedonian recipe dataset (36,237 recipes) for recipes that contain the ingredient. It uses the *original language* ingredient name for matching to preserve linguistic specificity. Returns up to 5 recipes with the ingredient's quantity and name as it appears in each recipe.

This step grounds the agent: instead of guessing that "павлака" means sour cream in all cases, it sees how the ingredient is actually used in Macedonian cooking.

### Stage 4 — Search USDA
Calls the **`search_usda_classes`** tool to retrieve candidate USDA food entries.

- The search query is the English translation from Stage 1. If context was fetched, an LLM optionally refines the query first (e.g. "sour cream" might become "sour cream cultured dairy" based on recipe context).
- The tool runs keyword + Jaccard similarity scoring across all **7,793 entries** in the USDA FoodData Central database (`usdaClasses.csv`). Tokens are extracted from both query and entry name; the score is `overlap / union`, with a small boost for long shared tokens.
- Returns the top 10 ranked candidates with their USDA IDs and similarity scores.

### Stage 5 — Evaluate & Select
Picks the best candidate from Stage 4.

- With an LLM: presents all candidates alongside the original ingredient, translation, and recipe context. The model selects the best match by index and returns a confidence score (0–100) and a written justification.
- Without an LLM: selects the candidate with the highest similarity score from Stage 4. Confidence is derived from that score.
- Output: the winning USDA ID and name, a confidence percentage, a confidence level (`high` / `med` / `low`), and a reasoning string.

---

## What you see on screen

### Single Ingredient Analysis
Type any ingredient (Macedonian or English) in the sidebar and click **Analyze**. The main area shows:

- Language flag and detected language
- Processing time in milliseconds
- All 5 stage cards in order, each colour-coded green (executed) or grey (skipped)
- The `get_recipe_context()` and `search_usda_classes()` tool calls shown as code blocks with their exact arguments and return summaries
- Context recipes in yellow cards showing recipe title and the ingredient's quantity/name from that recipe
- USDA candidates in a ranked list with similarity percentages; the selected entry highlighted in green
- Final USDA badge (`USDA:171257`) with full name
- Confidence bar (green = high, yellow = medium, red = low) with percentage
- Expandable **Agent's Reasoning** section with the LLM's written justification

### Full Recipe Analysis
Select a recipe from the sidebar dropdown (or search by title/tag). The main area shows:

- Recipe header with title, clickable tag buttons, source link, and image
- Expandable **Instructions** section
- **Ingredients** table with quantity and name
- **Analyze All Ingredients** button — runs the full pipeline on every ingredient in sequence, then shows:
  - A summary table: ingredient name → USDA entity → USDA ID → confidence emoji (🟢🟡🔴)
  - Individual expandable reasoning traces for every ingredient

### Recipe Browser
Clicking any tag button filters the recipe list to show all recipes with that tag in a 3-column grid with images, tag summaries, and ingredient counts. Clicking **View Recipe** loads that recipe into the main analysis view.

---

## LLM configuration

The app works in two modes depending on whether an OpenAI API key is configured:

| Mode | How to enable | What changes |
|---|---|---|
| **OpenAI** | Set `OPENAI_API_KEY` in `.env`, or enter a key at runtime via the API-key button | Stages 1, 2, 4 (query refinement), and 5 use `gpt-4o-mini` (override with `OPENAI_MODEL`) for translation, ambiguity reasoning, query formulation, and candidate evaluation with written justification |
| **Rule-based fallback** | No key set | Stage 1 uses the Macedonian dictionary; Stage 2 uses generic-term heuristics; Stage 4 uses the raw English translation as query; Stage 5 picks the top keyword match. All 5 stages still execute and display. |

The key is read from a `.env` file at the project root (copy `.env.example` to `.env`) or supplied at runtime through `POST /api/key` (held in memory only). `.env` is gitignored, so your key is never committed. The active engine is shown in the header.

---

## Data sources

| Source | Size | Purpose |
|---|---|---|
| `data/parsed_recipes.json` | 36,237 Macedonian recipes | Recipe context retrieval (Stage 3) |
| `data/usdaClasses.csv` | 7,793 food entries | USDA candidate search (Stage 4) |

---

## Project structure

```
backend/                     # the application package (agent + API)
├── agent.py                 # 5-stage pipeline, USDA search, recipe context tool
└── api.py                   # FastAPI backend — REST API + serves the web UI
frontend/                    # static roadmap web UI (served by api.py)
├── index.html
├── style.css
├── app.js
└── assets/
    └── sostojka-logo.png     # shared README and application logo
data/
├── parsed_recipes.json      # Macedonian recipe dataset
└── usdaClasses.csv          # USDA FoodData Central entries
docs/
└── BRANDING.md               # name, repository description, and logo source prompt
.env.example                 # copy to .env, then add your API key
requirements.txt
README.md
```

---


## REST API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/recipes?q=пилешко&tag=ручек&limit=20` | Search recipes (`q` = fuzzy title/tag, `tag` = exact category) |
| `GET /api/recipes/{index}` | Get a single recipe by dataset index |
| `GET /api/link/ingredient?name=павлака` | Run the full pipeline on one ingredient |
| `GET /api/link/recipe/{index}` | Run the pipeline on every ingredient in a recipe |
| `GET /api/smart?q=...` | Classify free-text as `ingredient` or `recipe` |
| `GET /api/stats` | Dataset statistics (total recipes, top tags) |
| `POST /api/key` | Configure an OpenAI key at runtime (held in memory only) |
