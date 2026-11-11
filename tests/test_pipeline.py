import io
import json
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.citator import (
    apply_deterministic_citation_gate,
    audit_draft_citations,
    verify_citation_courtlistener,
)
from backend.evaluator import enforce_deterministic_gate
from backend.main import app
from backend.retry_router import (
    load_all_rubrics,
    resolve_rubric_by_domain,
    run_gatekeeper_pipeline,
)
from backend.schemas import (
    EnablementCoaching,
    EvaluationReport,
    ProhibitedCheckResult,
    RequiredCheckResult,
)

client = TestClient(app)


def _make_sample_report(
    req_passed: bool,
    proh_violated: bool,
    claimed_status: str = "PASSED",
    proh_id: str = "PROH-FIN-01",
    proh_name: str = "Promissory or Guaranteed Return Language",
) -> EvaluationReport:
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
                id=proh_id,
                name=proh_name,
                violated=proh_violated,
                offending_quote="guaranteed 7.1%" if proh_violated else "None",
                remediation_instruction="Remove guaranteed return language."
                if proh_violated
                else "None",
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


def test_citator_anchored_placeholder_and_unanchored_extraction():
    source_notes = (
        "Supplied Authority: General Obligations Law § 15-301(1); "
        "Rose v. Spa Realty Assoc., 42 N.Y.2d 338 (1977)."
    )
    draft_text = (
        "Under Rose v. Spa Realty Assoc., 42 N.Y.2d 338 (1977), partial performance must be unequivocally referable. "
        "See also Smith v. Jones, 142 A.D.3d 101 [VERIFY CITATION Needed]. "
        "Furthermore, in Vance v. Horizon Corp., 218 A.D.3d 442 (1st Dep't 2024), the court rejected oral excuses."
    )

    items = audit_draft_citations(draft_text=draft_text, source_notes=source_notes)
    status_by_cite = {item.citation_text: item.status for item in items}

    assert any(
        "42 N.Y.2d 338" in cite and status == "ANCHORED"
        for cite, status in status_by_cite.items()
    )
    assert any(
        "142 A.D.3d 101" in cite and status == "PLACEHOLDER_FLAGGED"
        for cite, status in status_by_cite.items()
    )
    assert any(
        "218 A.D.3d 442" in cite and status == "UNANCHORED"
        for cite, status in status_by_cite.items()
    )


def test_citator_deterministic_override_forces_proh_leg_01_and_blocked():
    _, legal_rubric = resolve_rubric_by_domain("ny_litigation_drafting")
    source_notes = "Supplied Authority: Rose v. Spa Realty Assoc., 42 N.Y.2d 338 (1977)."
    hallucinated_draft = (
        "Under Rose v. Spa Realty Assoc., 42 N.Y.2d 338 (1977), and "
        "Pendelton v. Metro Holdings, 224 A.D.3d 819 (1st Dep't 2025), summary judgment is warranted."
    )

    # Simulate LLM missing the unanchored citation and returning PASSED
    false_pass_report = _make_sample_report(
        req_passed=True,
        proh_violated=False,
        claimed_status="PASSED",
        proh_id="PROH-LEG-01",
        proh_name="Unanchored Case Citations (Hallucination Gate)",
    )

    updated = apply_deterministic_citation_gate(
        report=false_pass_report,
        draft_text=hallucinated_draft,
        source_notes=source_notes,
        rubric=legal_rubric,
    )
    final_report = enforce_deterministic_gate(updated)

    assert final_report.gate_status == "BLOCKED"
    proh_01 = next(c for c in final_report.prohibited_checks if c.id == "PROH-LEG-01")
    assert proh_01.violated is True
    assert "224 A.D.3d 819" in proh_01.offending_quote
    assert len(final_report.citation_audit) == 2


@patch("urllib.request.urlopen")
def test_courtlistener_v4_lookup_verified_and_404(mock_urlopen, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "real_test_token_12345")

    # 1. Simulate 200 VERIFIED response from CourtListener v4
    mock_resp_ok = MagicMock()
    mock_resp_ok.__enter__.return_value.read.return_value = json.dumps(
        [
            {
                "citation": "42 N.Y.2d 338",
                "status": 200,
                "clusters": [
                    {"absolute_url": "/opinion/2592451/rose-v-spa-realty-assocs/"}
                ],
            }
        ]
    ).encode("utf-8")
    mock_urlopen.return_value = mock_resp_ok

    cl_status, cl_url = verify_citation_courtlistener("Rose v. Spa Realty Assoc., 42 N.Y.2d 338")
    assert cl_status == "VERIFIED"
    assert cl_url == "https://www.courtlistener.com/opinion/2592451/rose-v-spa-realty-assocs/"

    # 2. Simulate 404 NOT FOUND response for hallucinated case
    mock_resp_404 = MagicMock()
    mock_resp_404.__enter__.return_value.read.return_value = json.dumps(
        [{"citation": "999 N.Y.2d 999", "status": 404, "clusters": []}]
    ).encode("utf-8")
    mock_urlopen.return_value = mock_resp_404

    cl_status_404, cl_url_404 = verify_citation_courtlistener("Fake v. Case, 999 N.Y.2d 999")
    assert cl_status_404 == "NOT_FOUND_404"
    assert cl_url_404 is None


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
