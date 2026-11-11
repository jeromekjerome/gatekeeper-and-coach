# GEMINI.md — Gatekeeper & Coach (Enterprise AI Enablement Sandbox)

## Project Objective
Build a production-grade prototype for regulated enterprise teams (FINRA Wealth Management & NY/Federal Litigation) that pairs:
1. **A Deterministic Dual-Criteria Evaluation Loop:** Grades LLM drafts against explicit `required_criteria` (goals it must meet) and `prohibited_criteria` (violations it must not commit), blocking output and auto-retrying until every check passes.
2. **An Interactive Enablement Coach:** Diagnoses *why* the practitioner's original prompt caused compliance or hallucination failures, teaches them how to steer the model, and enforces a Human-in-the-Loop (HITL) verification gate before export.

## Tech Stack & Architectural Constraints
- **Backend:** Python 3.11+ with FastAPI (`backend/main.py`) and Pydantic v2 (`backend/schemas.py`) for strict structured JSON outputs.
- **Frontend:** Clean, dependency-free Vanilla JavaScript (`frontend/app.js`), HTML5 (`frontend/index.html`), and modern CSS (`frontend/styles.css`) served statically by FastAPI so the entire app runs with a single command (`uvicorn backend.main:app --reload`).
- **Model Routing:** Default to Google `google-genai` SDK (`gemini-2.5-pro` / `gemini-2.5-flash`) for zero-friction execution, with a clean adapter pattern allowing UI selection of Anthropic (`claude-sonnet`) or OpenAI (`gpt-4.1` / `o3-mini`) if API keys are present.
- **Code Style:** Modular, readable Python and JavaScript with explicit comments explaining evaluation gates and retry routing. Never hardcode compliance rules in Python; always load them dynamically from `backend/rubrics/*.json`.

## Core Invariants (Never Violate)
1. **Strict Gatekeeping:** The frontend "Unlock & Export Final Document" button MUST remain disabled (`disabled = true`) until:
   - `gate_status == "PASSED"` on all Required and Prohibited criteria.
   - All checkboxes in `enablement_coaching.human_verification_checklist` are manually checked by the human operator.
2. **Auditability:** Preserve and return every intermediate attempt (`attempt_1`, `attempt_2`) so the UI can display the blocked draft in red alongside the remediated draft in green.
3. **No Silent Fixes:** Whenever `retry_router.py` rewrites a blocked draft, `enablement_coaching` must explain to the non-technical user what prompt flaw triggered the initial violation.
