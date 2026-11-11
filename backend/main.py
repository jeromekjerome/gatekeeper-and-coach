import json
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables from .env at startup
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from backend.gamification import MASTERY_BADGES, PRACTITIONER_LEVELS
from backend.model_adapter import list_available_models
from backend.retry_router import load_all_rubrics, run_gatekeeper_pipeline
from backend.schemas import PipelineRequest, PipelineResponse

app = FastAPI(
    title="Gatekeeper & Coach — Enterprise AI Enablement Sandbox",
    description=(
        "Deterministic Dual-Criteria Evaluation Loop and Interactive Enablement Coach "
        "for FINRA Wealth Management & NY/Federal Litigation teams."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_DIR / "frontend"
SCENARIOS_PATH = BASE_DIR / "sample_scenarios" / "demo_scenarios.json"


@app.get("/api/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_workbench_config() -> Dict[str, Any]:
    """
    Returns dynamically loaded domain rubrics from backend/rubrics/*.json,
    sample demo scenarios from sample_scenarios/demo_scenarios.json,
    and available LLM models with their API key readiness status.
    """
    rubrics_map = load_all_rubrics()
    domains = [rubric_data for _, (_, rubric_data) in rubrics_map.items()]

    scenarios: Dict[str, Any] = {}
    if SCENARIOS_PATH.exists():
        with open(SCENARIOS_PATH, "r", encoding="utf-8") as f:
            scenarios = json.load(f)

    return {
        "domains": domains,
        "scenarios": scenarios,
        "models": list_available_models(),
        "gamification": {
            "levels": PRACTITIONER_LEVELS,
            "badges": list(MASTERY_BADGES.values()),
        },
    }


@app.post("/api/evaluate", response_model=PipelineResponse)
def evaluate_pipeline_endpoint(request: PipelineRequest) -> PipelineResponse:
    """
    Runs the Gatekeeper & Coach deterministic evaluation and auto-retry pipeline.
    """
    if not request.source_notes.strip():
        raise HTTPException(
            status_code=400, detail="Source notes (ground truth) cannot be empty."
        )
    if not request.practitioner_prompt.strip():
        raise HTTPException(
            status_code=400, detail="Practitioner prompt cannot be empty."
        )

    try:
        return run_gatekeeper_pipeline(
            domain_id=request.domain_id,
            source_notes=request.source_notes,
            practitioner_prompt=request.practitioner_prompt,
            model_id=request.model_id,
            current_xp=request.current_xp,
            current_streak=request.current_streak,
            unlocked_badges=request.unlocked_badges,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gatekeeper pipeline execution error: {str(exc)}",
        ) from exc


# Serve static frontend assets so the entire app runs with `uvicorn backend.main:app --reload`
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(str(index_file))


@app.get("/{filename:path}")
def serve_frontend_assets(filename: str):
    """
    Allows direct relative references such as /styles.css and /app.js from index.html.
    """
    candidate = (FRONTEND_DIR / filename).resolve()
    if (
        FRONTEND_DIR.resolve() in candidate.parents
        and candidate.is_file()
    ):
        return FileResponse(str(candidate))
    raise HTTPException(status_code=404, detail="Resource not found.")
