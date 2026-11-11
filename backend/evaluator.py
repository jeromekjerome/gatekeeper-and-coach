import json
from typing import Optional
from backend.schemas import EvaluationReport
from backend.model_adapter import generate_structured


def enforce_deterministic_gate(report: EvaluationReport) -> EvaluationReport:
    """
    Deterministic Dual-Criteria Gatekeeper Invariant:
    Regardless of how the LLM summarizes gate_status, verify in code that:
    1. Every required_check has passed == True.
    2. Every prohibited_check has violated == False.
    If any single check fails, force gate_status = 'BLOCKED'.
    """
    all_required_passed = len(report.required_checks) > 0 and all(
        check.passed for check in report.required_checks
    )
    no_prohibited_violated = all(
        not check.violated for check in report.prohibited_checks
    )

    if all_required_passed and no_prohibited_violated:
        report.gate_status = "PASSED"
    else:
        report.gate_status = "BLOCKED"

    return report


def evaluate_draft_and_coach(
    practitioner_prompt: str,
    source_notes: str,
    generated_draft: str,
    rubric_path: str,
    model_id: Optional[str] = "gemini-2.5-pro",
) -> EvaluationReport:
    """
    Grades a generated draft against explicit Required and Prohibited JSON criteria
    loaded dynamically from rubric_path, and generates enablement coaching for the
    non-technical practitioner.
    """
    with open(rubric_path, "r", encoding="utf-8") as f:
        rubric = json.load(f)

    system_instruction = (
        "You are an uncompromising Enterprise AI Compliance Gatekeeper and Enablement Coach. "
        "You evaluate AI-generated drafts for regulated professionals (FINRA financial advisors and NY litigation attorneys). "
        "1. Grade the GENERATED DRAFT strictly against every rule in the REQUIRED CRITERIA and PROHIBITED CRITERIA from the JSON rubric. "
        "   - Be sure to return one RequiredCheckResult for every item in required_criteria (matching its exact id and name). "
        "   - Be sure to return one ProhibitedCheckResult for every item in prohibited_criteria (matching its exact id and name). "
        "2. If ANY required criterion fails (passed=False) OR ANY prohibited criterion is violated (violated=True), "
        "you MUST set gate_status to 'BLOCKED'. Only set 'PASSED' if 100% of criteria pass. "
        "3. In enablement_coaching, speak directly to the non-technical practitioner: diagnose why their ORIGINAL PROMPT "
        "led the model astray (e.g., sycophancy, omission of risk/record anchors, or hallucination of unverified citations) "
        "and provide a concrete upgraded_prompt_template plus a 2-to-3 item human_verification_checklist."
    )

    user_payload = f"""
    === DOMAIN RUBRIC ===
    {json.dumps(rubric, indent=2)}

    === PRACTITIONER SOURCE NOTES (GROUND TRUTH) ===
    {source_notes}

    === PRACTITIONER'S ORIGINAL PROMPT ===
    {practitioner_prompt}

    === GENERATED DRAFT TO EVALUATE ===
    {generated_draft}
    """

    report = generate_structured(
        system_instruction=system_instruction,
        user_prompt=user_payload,
        response_schema=EvaluationReport,
        model_id=model_id or "gemini-2.5-pro",
        temperature=0.1,
    )

    return enforce_deterministic_gate(report)
