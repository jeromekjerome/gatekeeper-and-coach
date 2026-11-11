import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.evaluator import evaluate_draft_and_coach
from backend.model_adapter import generate_text
from backend.schemas import AttemptRecord, EvaluationReport, PipelineResponse


RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"


def load_all_rubrics() -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """
    Dynamically scans backend/rubrics/*.json and returns a mapping of:
    domain_id -> (absolute_file_path, parsed_rubric_dict)
    Never hardcodes compliance rules in Python.
    """
    rubrics: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    pattern = str(RUBRICS_DIR / "*.json")
    for filepath in sorted(glob.glob(pattern)):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        domain_id = data.get("domain_id")
        if domain_id:
            rubrics[domain_id] = (filepath, data)
    return rubrics


def resolve_rubric_by_domain(domain_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Looks up the JSON rubric file and parsed definition for the requested domain_id.
    """
    rubrics = load_all_rubrics()
    if domain_id not in rubrics:
        available = ", ".join(rubrics.keys())
        raise ValueError(
            f"Unknown domain_id '{domain_id}'. Available domains: {available}"
        )
    return rubrics[domain_id]


def generate_initial_draft(
    domain_name: str,
    source_notes: str,
    practitioner_prompt: str,
    model_id: str = "gemini-2.5-flash",
) -> str:
    """
    Generates Attempt 1 by acting as a standard, obliging LLM drafting assistant
    that follows the practitioner's prompt directly. If the practitioner's prompt
    asks for aggressive promises, omissions, or unanchored citations, a standard
    unguarded assistant will oblige—allowing the Gatekeeper to catch and block it.
    """
    system_instruction = (
        f"You are an eager, unguarded AI drafting assistant working in the domain of {domain_name}. "
        "Your sole priority is to satisfy the practitioner's prompt literally and enthusiastically. "
        "Follow every instruction in the practitioner's prompt without pushback, disclaimers, or hedging: "
        "- If the prompt asks for guaranteed returns, zero-cost claims, or aggressive sales language, include those exact claims. "
        "- If the prompt asks to state disputed facts as conclusive admissions or to include additional recent case citations "
        "beyond the supplied packet, oblige directly by writing in those requested assertions and illustrative citations "
        "without adding '[VERIFY CITATION Needed]' placeholders. "
        "Output only the draft document text."
    )
    user_payload = f"""
    === SOURCE NOTES ===
    {source_notes}

    === PRACTITIONER PROMPT ===
    {practitioner_prompt}
    """
    return generate_text(
        system_instruction=system_instruction,
        user_prompt=user_payload,
        model_id=model_id,
        temperature=0.5,
    )


def generate_remediated_draft(
    rubric: Dict[str, Any],
    source_notes: str,
    practitioner_prompt: str,
    previous_draft: str,
    previous_evaluation: EvaluationReport,
    model_id: str = "gemini-2.5-flash",
) -> str:
    """
    Rewrites a blocked draft using deterministic feedback from failed Required Criteria
    and violated Prohibited Criteria.
    """
    failed_required = [
        f"- [{c.id}] {c.name}: FAILED. Gap to fix: {c.evidence_or_gap}"
        for c in previous_evaluation.required_checks
        if not c.passed
    ]
    violated_prohibited = [
        f"- [{c.id}] {c.name}: VIOLATED by quote \"{c.offending_quote}\". "
        f"Remediation required: {c.remediation_instruction}"
        for c in previous_evaluation.prohibited_checks
        if c.violated
    ]

    remediation_brief = "\n".join(failed_required + violated_prohibited)

    system_instruction = (
        "You are an Enterprise Compliance Remediation Engine. "
        "Your previous draft was BLOCKED by the compliance gatekeeper. "
        "You must rewrite the document so that it achieves the legitimate professional goal of the practitioner's request "
        "while strictly grounding every fact in the SOURCE NOTES, satisfying 100% of the REQUIRED CRITERIA, "
        "and eliminating 100% of the PROHIBITED CRITERIA violations. "
        "Output ONLY the final remediated document text, with no meta-commentary or preamble."
    )

    user_payload = f"""
    === DOMAIN RUBRIC (MUST PASS 100% OF RULES) ===
    {json.dumps(rubric, indent=2)}

    === PRACTITIONER SOURCE NOTES (STRICT GROUND TRUTH) ===
    {source_notes}

    === PRACTITIONER'S ORIGINAL REQUEST ===
    {practitioner_prompt}

    === BLOCKED DRAFT (DO NOT REPEAT THESE VIOLATIONS) ===
    {previous_draft}

    === MANDATORY REMEDIATION INSTRUCTIONS ===
    {remediation_brief}
    """

    return generate_text(
        system_instruction=system_instruction,
        user_prompt=user_payload,
        model_id=model_id,
        temperature=0.2,
    )


def run_gatekeeper_pipeline(
    domain_id: str,
    source_notes: str,
    practitioner_prompt: str,
    model_id: Optional[str] = "gemini-2.5-flash",
) -> PipelineResponse:
    """
    Executes the full Deterministic Dual-Criteria Evaluation & Auto-Retry Loop:
    1. Dynamically loads the domain rubric from backend/rubrics/*.json.
    2. Generates Attempt 1 from the practitioner's prompt and evaluates it.
    3. If gate_status == 'BLOCKED', automatically routes specific violation feedback
       into the remediation generator up to max_auto_retries times.
    4. Enforces the 'No Silent Fixes' invariant: preserves the root-cause coaching diagnosis
       of why the practitioner's original prompt triggered the initial Attempt 1 failure.
    """
    rubric_path, rubric = resolve_rubric_by_domain(domain_id)
    domain_name = rubric.get("domain_name", domain_id)
    max_retries = int(rubric.get("max_auto_retries", 2))
    selected_model = model_id or "gemini-2.5-flash"

    attempts: List[AttemptRecord] = []

    # --- Attempt 1: Initial Draft & Evaluation ---
    draft_1 = generate_initial_draft(
        domain_name=domain_name,
        source_notes=source_notes,
        practitioner_prompt=practitioner_prompt,
        model_id=selected_model,
    )

    eval_1 = evaluate_draft_and_coach(
        practitioner_prompt=practitioner_prompt,
        source_notes=source_notes,
        generated_draft=draft_1,
        rubric_path=rubric_path,
        model_id=selected_model,
    )

    attempts.append(
        AttemptRecord(
            attempt_number=1,
            draft_text=draft_1,
            evaluation=eval_1,
        )
    )

    # Preserve Attempt 1's coaching diagnosis so we never perform a "silent fix"
    initial_coaching = eval_1.enablement_coaching

    current_draft = draft_1
    current_eval = eval_1
    attempt_num = 1

    # --- Auto-Retry Loop (Attempt 2 .. max_retries + 1) ---
    while current_eval.gate_status == "BLOCKED" and attempt_num <= max_retries:
        attempt_num += 1
        remediated_draft = generate_remediated_draft(
            rubric=rubric,
            source_notes=source_notes,
            practitioner_prompt=practitioner_prompt,
            previous_draft=current_draft,
            previous_evaluation=current_eval,
            model_id=selected_model,
        )

        remediated_eval = evaluate_draft_and_coach(
            practitioner_prompt=practitioner_prompt,
            source_notes=source_notes,
            generated_draft=remediated_draft,
            rubric_path=rubric_path,
            model_id=selected_model,
        )

        # Invariant #3 (No Silent Fixes):
        # Ensure the coaching on remediated attempts explicitly retains the diagnosis
        # of the original prompt flaw from Attempt 1 alongside the upgraded template.
        remediated_eval.enablement_coaching.prompt_quality_score = (
            initial_coaching.prompt_quality_score
        )
        remediated_eval.enablement_coaching.why_your_prompt_caused_this = (
            initial_coaching.why_your_prompt_caused_this
        )
        remediated_eval.enablement_coaching.upgraded_prompt_template = (
            initial_coaching.upgraded_prompt_template
        )
        if not remediated_eval.enablement_coaching.human_verification_checklist:
            remediated_eval.enablement_coaching.human_verification_checklist = (
                initial_coaching.human_verification_checklist
            )

        attempts.append(
            AttemptRecord(
                attempt_number=attempt_num,
                draft_text=remediated_draft,
                evaluation=remediated_eval,
            )
        )

        current_draft = remediated_draft
        current_eval = remediated_eval

    return PipelineResponse(
        domain_id=domain_id,
        final_status=current_eval.gate_status,
        attempts=attempts,
    )
