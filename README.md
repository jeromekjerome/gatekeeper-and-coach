# Gatekeeper & Coach

Gatekeeper & Coach is a compliance evaluation and prompt-diagnostics workbench for regulated professionals—specifically FINRA Rule 2210 wealth advisors and New York state and federal litigation attorneys—who use large language models to draft client communications and court filings from verified source records. It solves a single operational problem: when a practitioner's prompt pushes an LLM to promise returns, omit material risks, overstate disputed facts, or invent case citations, standard guardrails either silently rewrite the text or block it without explaining what in the prompt caused the violation, leaving the practitioner unable to audit the correction or avoid repeating the error.

## The problem

In regulated practice, drafting errors carry formal sanctions. A retail advisory email that quotes a 7.1% tax-equivalent yield on a municipal bond fund without pairing that figure with the fund's 8.4-year duration, 22% below-investment-grade allocation, and 0.68% expense ratio violates FINRA Rule 2210. A summary judgment reply brief under CPLR 3212 that upgrades a disputed hallway conversation into an undisputed admission or cites First Department cases outside the verified authority packet exposes counsel to sanctions.

Practitioners trigger these failures through normal prompting habits. An advisor asks for a punchy email that locks in a safe return; a litigator asks the model to destroy an opponent's defense and add recent appellate authority. Because instruction-tuned models are trained to satisfy user framing, they comply. Existing enterprise guardrails handle this poorly in two ways: post-hoc classifiers flag or rewrite output without preserving the rejected draft, and LLM-as-judge wrappers let a second model pass or fail a draft with no separation between what was checked by code, what was judged by a model, and what the human professional actually verified before signing off.

## What this builds

The system pairs two components in a single request lifecycle:

1. **Dual-criteria evaluation and remediation loop (`backend/retry_router.py`, `backend/evaluator.py`, `backend/citator.py`):** Every generated draft is evaluated against domain rules loaded at runtime from JSON rubrics (`backend/rubrics/*.json`). Each rubric defines `required_criteria` (affirmative disclosures, record anchors, or procedural standards that must appear in the draft) and `prohibited_criteria` (promissory language, unverified tax or legal advice, overstated record facts, or unanchored citations that must not appear). If any required check fails or any prohibited check is violated, the draft is marked `BLOCKED`, the specific evidence gaps and offending quotes are extracted into a remediation brief, and the router generates a revised draft up to `max_auto_retries` times (set to `2` in both domain rubrics).
2. **Interactive enablement coach (`backend/schemas.py`, `frontend/app.js`):** Alongside the rubric evaluation, the system grades the practitioner's original prompt (`0` to `100`), explains in plain language why the phrasing of that prompt induced the model to omit required disclosures or fabricate authority, provides a reusable upgraded prompt template, and generates a draft-specific verification checklist that the human professional must complete before export.

## How the gate works

The evaluation pipeline combines model-based rubric grading with code-enforced overrides. Qualitative compliance rules—such as whether a risk discussion is balanced against a yield claim (`REQ-FIN-01`) or whether an opposing party's contention is addressed (`REQ-LEG-03`)—are judged by the evaluator LLM returning structured JSON validated against `EvaluationReport`. Structural invariants, case-citation extraction, source-packet anchoring, and final gate aggregation are executed deterministically in Python code.

When the evaluator LLM and the Python gate disagree, the Python gate always wins in the restrictive direction:

- If the evaluator LLM sets `gate_status = "PASSED"`, `enforce_deterministic_gate()` in `backend/evaluator.py` inspects every item in `required_checks`, `prohibited_checks`, and `citation_audit`. If any single `RequiredCheckResult.passed` is `False`, any `ProhibitedCheckResult.violated` is `True`, or any `CitationAuditItem` has `status == "UNANCHORED"` or `courtlistener_status == "NOT_FOUND_404"`, Python overwrites `gate_status` to `"BLOCKED"`.
- In the litigation domain (`ny_litigation_drafting`), `apply_deterministic_citation_gate()` in `backend/citator.py` runs regular-expression extraction over the draft text to pull NY and federal reporter citations (`N.Y.2d`, `N.Y.3d`, `A.D.2d`, `A.D.3d`, `Misc. 3d`, `N.Y.S.3d`, `U.S.`, `F.3d`, `F.4th`, `F. Supp. 3d`) and `Party v. Party` case names. It normalizes each extracted citation and checks whether the reporter spine or party name exists in `source_notes` (`ANCHORED`) or is accompanied within a local character window by a `[VERIFY CITATION Needed]` placeholder (`PLACEHOLDER_FLAGGED`). Any citation that fails both checks is marked `UNANCHORED`. Even if the evaluator LLM marked `PROH-LEG-01` as clean (`violated = False`), `apply_deterministic_citation_gate()` overwrites `PROH-LEG-01` to `violated = True`, injects the offending citation list into `offending_quote` and `remediation_instruction`, and forces `gate_status = "BLOCKED"`.

| Layer | Module | Decision Mechanism | What It Decides | Conflict Resolution |
| :--- | :--- | :--- | :--- | :--- |
| Schema validation | `backend/schemas.py` | Deterministic (Pydantic v2) | Enforces type, field presence, and enum constraints on `EvaluationReport` and `PipelineResponse`. | Rejects malformed model JSON before gate evaluation. |
| Qualitative rubric grading | `backend/evaluator.py` | Model judgement (LLM structured output at `temperature=0.1`) | Grades each `REQ-*` (`passed: bool`, `evidence_or_gap`) and `PROH-*` (`violated: bool`, `offending_quote`, `remediation_instruction`) against `source_notes`. | Subject to downstream Python override; cannot force `PASSED` if any individual check fails. |
| Citation extraction and record anchoring | `backend/citator.py` | Deterministic (Python regex + normalized string matching against `source_notes`) | Extracts reporter and case citations from the draft; classifies each as `ANCHORED`, `PLACEHOLDER_FLAGGED`, or `UNANCHORED`. | Overwrites `PROH-LEG-01` to `violated = True` and forces `gate_status = "BLOCKED"` on any `UNANCHORED` citation. |
| External authority lookup | `backend/citator.py` | External API + deterministic status check (CourtListener v4 `citation-lookup`) | When `COURTLISTENER_TOKEN` is set, verifies whether extracted citations resolve to real opinions (`VERIFIED` vs. `NOT_FOUND_404`). | Any `NOT_FOUND_404` result forces `PROH-LEG-01` to `violated = True` and `gate_status = "BLOCKED"`. |
| Gate status aggregation | `backend/evaluator.py` (`enforce_deterministic_gate`) | Deterministic (Python boolean conjunction) | Computes final `gate_status` (`PASSED` only when 100% of required checks pass, 0 prohibited checks are violated, and 0 citations are unanchored or 404). | Overwrites any false-positive `PASSED` summary from the evaluator model. |
| Export lock | `frontend/app.js` (`enforceStrictExportGate`) | Deterministic (client state machine) | Controls `exportDocBtn.disabled`. | Keeps export locked unless `final_status === "PASSED"`, all final checks pass, and 100% of human verification checkboxes are checked. |

## Human-in-the-loop

The interface enforces three operational rules so that automated remediation never hides model behavior from the signing professional:

1. **Full attempt history:** `run_gatekeeper_pipeline()` returns every intermediate `AttemptRecord` (`attempt_1`, `attempt_2`, up to `max_auto_retries + 1`). The center panel renders the blocked Attempt 1 draft in red—alongside its missing requirements and verbatim offending quotes—directly above the remediated Attempt 2 draft in green.
2. **No silent fixes:** When Attempt 1 is blocked and Attempt 2 passes after auto-remediation, `backend/retry_router.py` copies Attempt 1's `prompt_quality_score`, `why_your_prompt_caused_this`, and `upgraded_prompt_template` onto the remediated evaluation record. The practitioner always sees the diagnosis of why their original prompt caused the initial violation rather than receiving a silently corrected document.
3. **Dual-condition export lock:** In `frontend/app.js`, the "Unlock & Export Final Document" button (`#exportDocBtn`) initializes with `disabled = true`. `enforceStrictExportGate()` re-evaluates on every state change and only enables export when `final_status === "PASSED"` (verified across all required checks, prohibited checks, and citation audits on the final attempt) and every checkbox in `enablement_coaching.human_verification_checklist` has been manually checked by the operator. Exporting generates a text certificate recording the UTC timestamp, domain ID, attempt count, and the operator's checked attestations above the final draft.

## Architecture and stack

The backend is built on Python 3.11+, FastAPI (`backend/main.py`), and Pydantic v2 (`backend/schemas.py`). Domain compliance rules are stored as declarative JSON files in `backend/rubrics/*.json` and loaded dynamically on each request so compliance logic is decoupled from application code. Model calls route through `backend/model_adapter.py`, which defaults to Google's `google-genai` SDK (`gemini-2.5-flash` and `gemini-2.5-pro` via `v1beta` structured outputs), supports Anthropic (`claude-sonnet-4-20250514`) and OpenAI (`gpt-4.1`, `o3-mini`) over REST when keys are present in `.env`, and fails over across configured models on retriable HTTP (`404`, `408`, `410`, `429`, `5xx`), timeout, or connection errors. The frontend (`frontend/index.html`, `frontend/app.js`, `frontend/styles.css`) is written in dependency-free vanilla JavaScript and served statically by FastAPI so the entire application runs in one process.

```text
gatekeeper-and-coach/
├── backend/                  # FastAPI application, evaluation pipeline, citator, and model routing
│   ├── rubrics/              # Declarative JSON compliance rubrics (FINRA Rule 2210 and NY litigation)
│   ├── citator.py            # Regex legal citation extractor, source-packet matcher, and CourtListener v4 client
│   ├── evaluator.py          # Structured LLM rubric evaluator and Python gate-status override
│   ├── gamification.py       # Deterministic XP, streak, and badge calculation for first-pass compliance
│   ├── main.py               # FastAPI endpoints (/api/config, /api/evaluate, /api/health/models) and static mount
│   ├── model_adapter.py      # Multi-provider adapter (Gemini, Claude, OpenAI) with automatic failover chain
│   ├── retry_router.py       # Attempt 1 generation, remediation brief construction, and retry orchestration
│   └── schemas.py            # Pydantic v2 request/response contracts and evaluation schemas
├── frontend/                 # Dependency-free vanilla JS, HTML5, and CSS three-column workbench
├── sample_scenarios/         # Adversarial FINRA and NY CPLR 3212 demo scenarios (demo_scenarios.json)
├── tests/                    # Pytest unit and integration suite covering gates, citator, failover, and API
├── run_eval_suite.py         # CLI regression harness with rubric-aware SHA-256 caching and --dry-run mode
├── requirements.txt          # Python package dependencies
└── .env.example              # Template for provider API keys and optional CourtListener token
```

## Running it locally

1. Install dependencies (Python 3.11+):

```bash
pip install -r requirements.txt
```

2. Create `.env` from `.env.example` and populate at least one model provider key:

```bash
cp .env.example .env
```

Environment variables defined in `.env.example`:
- `GEMINI_API_KEY`: Google Gemini API key (used by default for `gemini-2.5-flash` and `gemini-2.5-pro`).
- `OPENAI_API_KEY`: Optional OpenAI API key (enables `gpt-4.1` and `o3-mini`).
- `ANTHROPIC_API_KEY`: Optional Anthropic API key (enables `claude-sonnet`).
- `COURTLISTENER_TOKEN`: Optional CourtListener v4 API token (enables live `citation-lookup` verification in `backend/citator.py`; if omitted, `courtlistener_status` defaults to `UNCHECKED` and citation gating relies on `source_notes` anchoring and placeholder checks).

Optional runtime environment variables supported by `backend/model_adapter.py`:
- `GATEKEEPER_MODEL_FALLBACKS`: Comma-separated model ID order for failover (defaults to `gemini-2.5-flash,gemini-2.5-pro,gpt-4.1,claude-sonnet,o3-mini`).
- `MODEL_HEALTH_TTL_SECONDS`: Cache TTL in seconds for `/api/health/models` readiness snapshots (defaults to `60`).

3. Start the server:

```bash
uvicorn backend.main:app --reload
```

4. Open `http://127.0.0.1:8000` in a browser. Use the **Model Router** dropdown in the top bar (or pass `model_id` in `POST /api/evaluate`) to select `gemini-2.5-flash`, `gemini-2.5-pro`, `claude-sonnet`, `gpt-4.1`, or `o3-mini`. If a selected provider's API key is not configured in `.env`, `backend/model_adapter.py` routes the call to the first configured provider in the failover chain and reports the executed model in `model_routing`.

5. Run the unit test suite and CLI regression harness:

```bash
python -m pytest tests/test_pipeline.py -v
python run_eval_suite.py --dry-run
```

## Evaluation

### Implemented regression harness

The repository includes an adversarial scenario file (`sample_scenarios/demo_scenarios.json`) containing 2 scenarios—one FINRA Rule 2210 municipal bond pitch (`finra_scenario`) and one New York Supreme Court CPLR 3212 summary judgment reply (`legal_scenario`)—paired with `run_eval_suite.py`. The CLI harness runs scenarios concurrently via `ThreadPoolExecutor`, caches `PipelineResponse` payloads by a 16-character SHA-256 digest computed over `(domain_id, source_notes, practitioner_prompt, model_id, rubric_sha256)` so any edit to `backend/rubrics/*.json` invalidates stale cache entries, and verifies three invariants per scenario:
- `attempt_1_intercepted`: Attempt 1 is marked `BLOCKED`.
- `final_remediated_passed`: The final attempt after remediation is marked `PASSED`.
- `no_silent_fixes_preserved`: The final attempt retains Attempt 1's `why_your_prompt_caused_this` diagnosis.

### Planned measurement

A broader labelled benchmark set is not yet checked into the repository. Planned evaluation before production deployment measures gate accuracy and practitioner review efficiency across `[N]` labelled drafts (`[N_finra]` FINRA retail communications and `[N_legal]` NY motion and appellate drafts) annotated by domain reviewers with ground-truth rule violations:

- **Seeded compliance and citation violations caught on Attempt 1:** `[X of Y]` (`[X]%`)
- **Unanchored case citations caught deterministically by `backend/citator.py`:** `[X of Y]` (`[X]%`)
- **False blocks on compliant first-pass drafts:** `[X of Y]` (`[X]%`)
- **Violations missed by unaided human review but caught by the gate:** `[X of Y]` (`[X]%`)
- **Remediation convergence rate within `max_auto_retries` (2 retries):** `[X of Y]` (`[X]%`)
- **Mean practitioner review and revision time saved per draft:** `[X]` minutes per draft (from `[X]` minutes baseline to `[X]` minutes with gate and coach)

## Design trade-offs and what is not built

- **Intentional Attempt 1 compliance vulnerability:** `generate_initial_draft()` in `backend/retry_router.py` uses a system prompt that instructs the drafting model to follow the practitioner's prompt literally without hedging. This is a deliberate design choice for an enablement sandbox so that flawed prompts reliably demonstrate violations on Attempt 1 rather than being silently softened by model provider safety tuning. A production drafting system would apply the compliance rubric during Attempt 1 generation as well.
- **Same-model generation and evaluation by default:** Unless configured otherwise, the selected `model_id` is used for both drafting (`generate_initial_draft` / `generate_remediated_draft`) and evaluation (`evaluate_draft_and_coach`). When a single model family has a systematic blind spot on a qualitative rubric rule, it can miss that violation in both roles. Production deployments should separate the generator model from the evaluator model across distinct providers or pin `gemini-2.5-pro` / `claude-sonnet` strictly for evaluation.
- **Regex scope in `backend/citator.py`:** The deterministic citation parser extracts standard NY and federal case reporters (`N.Y.2d`, `N.Y.3d`, `A.D.2d`, `A.D.3d`, `Misc. 3d`, `N.Y.S.3d`, `U.S.`, `S. Ct.`, `L. Ed. 2d`, `F.2d`, `F.3d`, `F.4th`, `F. Supp. 2d`, `F. Supp. 3d`) and `Party v. Party` strings. It does not deterministically parse statutory citations (such as `CPLR 3212` or `GOL § 15-301(1)`), administrative regulatory notices, state reporters outside New York, or `Id.` / `supra` short-form references. Statutory and factual record anchoring (`REQ-LEG-01`, `PROH-LEG-02`) still rely on the evaluator LLM.
- **Synchronous request lifecycle:** `POST /api/evaluate` runs Attempt 1 generation, Attempt 1 evaluation, and up to two remediation/evaluation cycles synchronously inside a single HTTP request. A run that requires two retries makes up to six sequential LLM calls, which can take 15 to 45 seconds and lacks streaming progress updates (SSE or WebSockets).
- **No server-side persistence or authentication:** Audit trails, export certificates, and practitioner gamification state (`gatekeeper_coach_mastery_v1`) live in memory or browser `localStorage`. Once the browser tab closes, the signed export certificate exists only in the downloaded `.txt` file. A production deployment for a regulated broker-dealer or law firm requires authenticated user identity (SSO/RBAC), an append-only server-side audit log (for FINRA Rule 4511 / SEC Rule 17a-4 record retention), and PDF/DOCX document ingestion rather than plain-text `source_notes` pasting.

## Author

Built by Jerome ([www.jwd.nyc.](http://www.jwd.nyc./)).
