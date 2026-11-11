from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.evaluator import enforce_deterministic_gate
from backend.main import app
from backend.retry_router import load_all_rubrics, resolve_rubric_by_domain, run_gatekeeper_pipeline
from backend.schemas import (
    EnablementCoaching,
    EvaluationReport,
    ProhibitedCheckResult,
    RequiredCheckResult,
)

client = TestClient(app)


def _make_sample_report(req_passed: bool, proh_violated: bool, claimed_status: str = "PASSED") -> EvaluationReport:
    return EvaluationReport(
        gate_status=claimed_status,  # type: ignore[arg-type]
        required_checks=[
            RequiredCheckResult(
                id="REQ-FIN-01",
                name="Balanced Risk-Reward Presentation",
                passed=req_passed,
                evidence_or_gap="Sample evidence or gap",
            )
        ],
        prohibited_checks=[
            ProhibitedCheckResult(
                id="PROH-FIN-01",
                name="Promissory or Guaranteed Return Language",
                violated=proh_violated,
                offending_quote="guaranteed 7.1%" if proh_violated else "None",
                remediation_instruction="Remove guaranteed return language." if proh_violated else "None",
            )
        ],
        enablement_coaching=EnablementCoaching(
            prompt_quality_score=25,
            why_your_prompt_caused_this="Your original prompt instructed the model to claim a 'guaranteed 7.1% return'.",
            upgraded_prompt_template="Draft a balanced FINRA 2210 compliant memo...",
            human_verification_checklist=[
                "Confirm 0.68% expense ratio is stated accurately.",
                "Verify client's 5-7 year horizon matches liquidity needs.",
            ],
        ),
    )


def test_dynamic_rubric_loading():
    rubrics = load_all_rubrics()
    assert "finra_wealth_advisory" in rubrics
    assert "ny_litigation_drafting" in rubrics

    _, finra_data = resolve_rubric_by_domain("finra_wealth_advisory")
    assert len(finra_data["required_criteria"]) == 3
    assert len(finra_data["prohibited_criteria"]) == 3


def test_deterministic_gate_overrides_false_positive():
    # Even if LLM claims PASSED, a failed required check must force BLOCKED
    r1 = _make_sample_report(req_passed=False, proh_violated=False, claimed_status="PASSED")
    assert enforce_deterministic_gate(r1).gate_status == "BLOCKED"

    # Even if LLM claims PASSED, a violated prohibited check must force BLOCKED
    r2 = _make_sample_report(req_passed=True, proh_violated=True, claimed_status="PASSED")
    assert enforce_deterministic_gate(r2).gate_status == "BLOCKED"

    # Only when 100% required pass and 0 prohibited are violated does it pass
    r3 = _make_sample_report(req_passed=True, proh_violated=False, claimed_status="BLOCKED")
    assert enforce_deterministic_gate(r3).gate_status == "PASSED"


@patch("backend.retry_router.evaluate_draft_and_coach")
@patch("backend.retry_router.generate_remediated_draft")
@patch("backend.retry_router.generate_initial_draft")
def test_retry_router_auditability_and_no_silent_fixes(
    mock_initial_draft,
    mock_remediated_draft,
    mock_evaluate,
):
    mock_initial_draft.return_value = "Move $500k now for a guaranteed 7.1% risk-free yield!"
    mock_remediated_draft.return_value = (
        "Compliant memo discussing 7.1% tax-equivalent yield alongside 8.4-year duration, "
        "22% BBB credit risk, 0.68% expense ratio, and 5-7 year time horizon disclosures."
    )

    eval_attempt_1 = _make_sample_report(req_passed=False, proh_violated=True, claimed_status="BLOCKED")
    eval_attempt_2 = _make_sample_report(req_passed=True, proh_violated=False, claimed_status="PASSED")
    # Simulate LLM giving a generic coaching message on Attempt 2 to verify 'No Silent Fixes' preserves Attempt 1's diagnosis
    eval_attempt_2.enablement_coaching.why_your_prompt_caused_this = "Generic message"
    eval_attempt_2.enablement_coaching.prompt_quality_score = 95

    mock_evaluate.side_effect = [eval_attempt_1, eval_attempt_2]

    response = run_gatekeeper_pipeline(
        domain_id="finra_wealth_advisory",
        source_notes="Client Margaret Vance, 68...",
        practitioner_prompt="Tell her it's a guaranteed 7.1% return!",
        model_id="gemini-2.5-flash",
    )

    assert response.final_status == "PASSED"
    assert len(response.attempts) == 2
    assert response.attempts[0].attempt_number == 1
    assert response.attempts[0].evaluation.gate_status == "BLOCKED"
    assert response.attempts[1].attempt_number == 2
    assert response.attempts[1].evaluation.gate_status == "PASSED"

    # Invariant #3: No Silent Fixes — Attempt 2 retains Attempt 1's original prompt flaw diagnosis
    assert (
        response.attempts[1].evaluation.enablement_coaching.why_your_prompt_caused_this
        == "Your original prompt instructed the model to claim a 'guaranteed 7.1% return'."
    )
    assert response.attempts[1].evaluation.enablement_coaching.prompt_quality_score == 25


def test_fastapi_endpoints():
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    config = client.get("/api/config")
    assert config.status_code == 200
    payload = config.json()
    assert len(payload["domains"]) == 2
    assert "finra_scenario" in payload["scenarios"]
    assert "legal_scenario" in payload["scenarios"]
    assert len(payload["models"]) >= 2

    index_page = client.get("/")
    assert index_page.status_code == 200
    assert "Gatekeeper &amp; Coach" in index_page.text
    assert 'id="exportDocBtn"' in index_page.text
    assert "disabled" in index_page.text
