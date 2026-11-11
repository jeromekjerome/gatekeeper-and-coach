#!/usr/bin/env python3
"""
run_eval_suite.py — Cached Adversarial Evaluation & Regression Harness CLI
for Gatekeeper & Coach.

Features:
- SHA-256 deterministic response caching keyed by (domain_id, source_notes,
  practitioner_prompt, model_id, rubric_sha256) so rubric edits auto-invalidate cache.
- Concurrent execution across scenarios via ThreadPoolExecutor (--concurrency).
- Incremental JSON report flushing after each scenario completes (--output).
- Zero-cost offline validation mode (--dry-run) verifying rubric alignment,
  Attempt 1 interception, Attempt 2 remediation, and 'No Silent Fixes' invariants.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.citator import enforce_deterministic_citation_gate
from backend.evaluator import enforce_deterministic_gate
from backend.gamification import score_enablement_run
from backend.retry_router import resolve_rubric_by_domain, run_gatekeeper_pipeline
from backend.schemas import (
    AttemptRecord,
    EnablementCoaching,
    EvaluationReport,
    PipelineResponse,
    ProhibitedCheckResult,
    RequiredCheckResult,
)


def compute_scenario_cache_key(
    domain_id: str,
    source_notes: str,
    practitioner_prompt: str,
    model_id: str,
    rubric_dict: Dict[str, Any],
) -> str:
    """
    Computes a deterministic 16-char SHA-256 cache key over the scenario inputs
    AND the active JSON rubric definition so any rubric change invalidates stale cache.
    """
    rubric_canonical = json.dumps(rubric_dict, sort_keys=True)
    payload = json.dumps(
        {
            "domain_id": domain_id,
            "source_notes": source_notes.strip(),
            "practitioner_prompt": practitioner_prompt.strip(),
            "model_id": model_id,
            "rubric_sha256": hashlib.sha256(rubric_canonical.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_cache_file(cache_path: Path, disable_cache: bool = False) -> Dict[str, Any]:
    if disable_cache or not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache_file(cache_path: Path, cache_data: Dict[str, Any], disable_cache: bool = False) -> None:
    if disable_cache:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)


def build_dry_run_pipeline_response(
    domain_id: str,
    source_notes: str,
    practitioner_prompt: str,
    rubric: Dict[str, Any],
) -> PipelineResponse:
    """
    Constructs a deterministic synthetic 2-attempt PipelineResponse (Attempt 1 BLOCKED ->
    Attempt 2 PASSED) exercising enforce_deterministic_citation_gate, enforce_deterministic_gate,
    and score_enablement_run without making live network LLM calls.
    """
    req_rules = rubric.get("required_criteria", [])
    proh_rules = rubric.get("prohibited_criteria", [])

    if domain_id == "ny_litigation_drafting":
        draft_1 = (
            "Defendant conclusively admitted in his deposition that he fabricated the hallway conversation. "
            "Under Smith v. Jones, 142 A.D.3d 101 (1st Dept 2024), summary judgment must be granted."
        )
    else:
        draft_1 = (
            "Move $500k out of your sleepy Treasury fund right away into our NY High-Yield Muni Fund "
            "for a safe, guaranteed 7.1% tax-free return with zero downside and zero cost to switch!"
        )

    eval_1 = EvaluationReport(
        gate_status="BLOCKED",
        required_checks=[
            RequiredCheckResult(
                id=r["id"],
                name=r["name"],
                passed=False,
                evidence_or_gap=f"Attempt 1 omitted required standard for {r['id']}.",
            )
            for r in req_rules
        ],
        prohibited_checks=[
            ProhibitedCheckResult(
                id=p["id"],
                name=p["name"],
                violated=True,
                offending_quote=(p.get("violation_examples") or ["Violation quote"])[0],
                remediation_instruction=f"Remove prohibited language violating {p['id']}.",
            )
            for p in proh_rules
        ],
        enablement_coaching=EnablementCoaching(
            prompt_quality_score=28,
            why_your_prompt_caused_this=(
                f"Your prompt ('{practitioner_prompt[:60]}...') explicitly instructed the model "
                "to make unanchored or non-compliant assertions."
            ),
            upgraded_prompt_template=(
                f"Using only the Verified Source Notes for {domain_id}, draft a compliant document "
                "satisfying all required disclosures and citing only supplied authorities."
            ),
            human_verification_checklist=[
                "Verify all numerical figures and dates against the source packet.",
                "Confirm all regulatory or procedural disclosures are complete.",
            ],
        ),
    )
    eval_1 = enforce_deterministic_citation_gate(eval_1, draft_1, source_notes)
    eval_1 = enforce_deterministic_gate(eval_1)

    if domain_id == "ny_litigation_drafting":
        draft_2 = (
            "Under CPLR 3212, Plaintiff is entitled to summary judgment based on the signed Guaranty "
            "(Exhibit A) and Tenant ledger showing $142,500 unpaid base rent (Exhibit B). While Defendant "
            "alleges an oral modification (Pendelton Opp. Aff. ¶ 11), Lease § 24.3 (Exhibit C) and "
            "General Obligations Law § 15-301(1) bar oral modifications, and under Rose v. Spa Realty Assoc., "
            "42 N.Y.2d 338 (1977), remaining in possession through Feb 2026 is not unequivocally referable."
        )
    else:
        draft_2 = (
            "For your 5-7 year horizon and $45,000/yr income need, the NY Tax-Exempt High-Yield Municipal "
            "Bond Fund offers a 7.1% tax-equivalent yield alongside an 8.4-year duration, 22% BBB credit risk, "
            "and 0.68% expense ratio. Past performance does not guarantee future results; consult your CPA."
        )

    eval_2 = EvaluationReport(
        gate_status="PASSED",
        required_checks=[
            RequiredCheckResult(
                id=r["id"],
                name=r["name"],
                passed=True,
                evidence_or_gap="Verified in remediated draft.",
            )
            for r in req_rules
        ],
        prohibited_checks=[
            ProhibitedCheckResult(
                id=p["id"],
                name=p["name"],
                violated=False,
                offending_quote="None",
                remediation_instruction="None",
            )
            for p in proh_rules
        ],
        enablement_coaching=EnablementCoaching(
            prompt_quality_score=eval_1.enablement_coaching.prompt_quality_score,
            why_your_prompt_caused_this=eval_1.enablement_coaching.why_your_prompt_caused_this,
            upgraded_prompt_template=eval_1.enablement_coaching.upgraded_prompt_template,
            human_verification_checklist=eval_1.enablement_coaching.human_verification_checklist,
        ),
    )
    eval_2 = enforce_deterministic_citation_gate(eval_2, draft_2, source_notes)
    eval_2 = enforce_deterministic_gate(eval_2)

    attempts = [
        AttemptRecord(attempt_number=1, draft_text=draft_1, evaluation=eval_1),
        AttemptRecord(attempt_number=2, draft_text=draft_2, evaluation=eval_2),
    ]
    reward = score_enablement_run(domain_id=domain_id, attempts=attempts)
    return PipelineResponse(
        domain_id=domain_id,
        final_status=eval_2.gate_status,
        attempts=attempts,
        enablement_reward=reward,
    )


def evaluate_single_scenario(
    scenario_key: str,
    scenario_data: Dict[str, Any],
    model_id: str,
    cache: Dict[str, Any],
    cache_lock: threading.Lock,
    dry_run: bool = False,
    no_cache: bool = False,
) -> Dict[str, Any]:
    domain_id = scenario_data["domain_id"]
    source_notes = scenario_data["source_notes"]
    flawed_prompt = scenario_data["flawed_prompt"]
    title = scenario_data.get("title", scenario_key)

    _, rubric = resolve_rubric_by_domain(domain_id)
    key = compute_scenario_cache_key(
        domain_id=domain_id,
        source_notes=source_notes,
        practitioner_prompt=flawed_prompt,
        model_id="dry-run" if dry_run else model_id,
        rubric_dict=rubric,
    )

    cache_hit = False
    pipeline_resp: Optional[PipelineResponse] = None

    with cache_lock:
        if not no_cache and key in cache:
            pipeline_resp = PipelineResponse.model_validate(cache[key])
            cache_hit = True

    if pipeline_resp is None:
        if dry_run:
            pipeline_resp = build_dry_run_pipeline_response(
                domain_id=domain_id,
                source_notes=source_notes,
                practitioner_prompt=flawed_prompt,
                rubric=rubric,
            )
        else:
            pipeline_resp = run_gatekeeper_pipeline(
                domain_id=domain_id,
                source_notes=source_notes,
                practitioner_prompt=flawed_prompt,
                model_id=model_id,
            )
        with cache_lock:
            cache[key] = pipeline_resp.model_dump()

    attempts = pipeline_resp.attempts
    attempt_1_blocked = len(attempts) > 0 and attempts[0].evaluation.gate_status == "BLOCKED"
    final_passed = pipeline_resp.final_status == "PASSED"
    no_silent_fixes_ok = (
        len(attempts) >= 2
        and attempts[-1].evaluation.enablement_coaching.why_your_prompt_caused_this
        == attempts[0].evaluation.enablement_coaching.why_your_prompt_caused_this
        and bool(attempts[-1].evaluation.enablement_coaching.why_your_prompt_caused_this.strip())
    ) or len(attempts) == 1

    return {
        "scenario_key": scenario_key,
        "title": title,
        "domain_id": domain_id,
        "cache_key": key,
        "cache_hit": cache_hit,
        "total_attempts": len(attempts),
        "attempt_1_gate_status": attempts[0].evaluation.gate_status if attempts else None,
        "final_status": pipeline_resp.final_status,
        "invariants": {
            "attempt_1_intercepted": attempt_1_blocked,
            "final_remediated_passed": final_passed,
            "no_silent_fixes_preserved": no_silent_fixes_ok,
        },
        "pipeline_response": pipeline_resp.model_dump(),
    }


def run_suite(
    input_path: Path,
    output_path: Path,
    cache_path: Path,
    domain_filter: Optional[str] = None,
    model_id: str = "gemini-2.5-flash",
    concurrency: int = 2,
    dry_run: bool = False,
    no_cache: bool = False,
) -> Dict[str, Any]:
    start_ts = time.time()
    with open(input_path, "r", encoding="utf-8") as f:
        raw_scenarios = json.load(f)

    scenarios_items = [
        (k, v)
        for k, v in raw_scenarios.items()
        if not domain_filter or v.get("domain_id") == domain_filter
    ]

    cache = load_cache_file(cache_path, disable_cache=no_cache)
    cache_lock = threading.Lock()
    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        future_map = {
            pool.submit(
                evaluate_single_scenario,
                k,
                v,
                model_id,
                cache,
                cache_lock,
                dry_run,
                no_cache,
            ): k
            for k, v in scenarios_items
        }
        for fut in as_completed(future_map):
            res = fut.result()
            results.append(res)
            save_cache_file(cache_path, cache, disable_cache=False)

    results.sort(key=lambda r: r["scenario_key"])
    total = len(results)
    intercepted = sum(1 for r in results if r["invariants"]["attempt_1_intercepted"])
    final_passed = sum(1 for r in results if r["invariants"]["final_remediated_passed"])
    no_silent_fixes = sum(1 for r in results if r["invariants"]["no_silent_fixes_preserved"])
    cache_hits = sum(1 for r in results if r["cache_hit"])

    report = {
        "suite_summary": {
            "total_scenarios": total,
            "attempt_1_intercepted_count": intercepted,
            "final_passed_count": final_passed,
            "no_silent_fixes_verified_count": no_silent_fixes,
            "cache_hits": cache_hits,
            "all_invariants_passed": total > 0
            and intercepted == total
            and final_passed == total
            and no_silent_fixes == total,
            "dry_run": dry_run,
            "model_id": model_id,
            "elapsed_sec": round(time.time() - start_ts, 3),
        },
        "scenarios": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gatekeeper & Coach — Cached Adversarial Evaluation & Regression Harness"
    )
    parser.add_argument(
        "--input",
        default=str(BASE_DIR / "sample_scenarios" / "demo_scenarios.json"),
        help="Path to adversarial scenarios JSON file",
    )
    parser.add_argument(
        "--output",
        default=str(BASE_DIR / "tests" / "eval_results.json"),
        help="Path to write evaluation report JSON",
    )
    parser.add_argument(
        "--cache",
        default=str(BASE_DIR / "tests" / ".eval_cache.json"),
        help="Path to SHA-256 response cache file",
    )
    parser.add_argument("--domain", default=None, help="Filter by domain_id")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Model ID to evaluate")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel worker count")
    parser.add_argument("--dry-run", action="store_true", help="Run deterministic offline verification")
    parser.add_argument("--no-cache", action="store_true", help="Bypass existing cache on read and refresh cache")
    args = parser.parse_args()

    report = run_suite(
        input_path=Path(args.input),
        output_path=Path(args.output),
        cache_path=Path(args.cache),
        domain_filter=args.domain,
        model_id=args.model,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
    )

    summary = report["suite_summary"]
    print("================================================================")
    print("GATEKEEPER & COACH — ADVERSARIAL REGRESSION SUITE SUMMARY")
    print("================================================================")
    for sc in report["scenarios"]:
        hit_label = "[CACHE HIT]" if sc["cache_hit"] else "[EXECUTED]"
        inv = sc["invariants"]
        ok = all(inv.values())
        badge = "PASS" if ok else "FAIL"
        print(
            f"  [{badge}] {sc['scenario_key']} ({sc['domain_id']}) {hit_label} | "
            f"Attempt 1: {sc['attempt_1_gate_status']} -> Final: {sc['final_status']}"
        )
    print("----------------------------------------------------------------")
    print(f"  Scenarios Run          : {summary['total_scenarios']}")
    print(f"  Attempt 1 Intercepted  : {summary['attempt_1_intercepted_count']}/{summary['total_scenarios']}")
    print(f"  Final Remediated Pass  : {summary['final_passed_count']}/{summary['total_scenarios']}")
    print(f"  No Silent Fixes Intact : {summary['no_silent_fixes_verified_count']}/{summary['total_scenarios']}")
    print(f"  Cache Hits             : {summary['cache_hits']}/{summary['total_scenarios']}")
    print(f"  Elapsed                : {summary['elapsed_sec']}s")
    print("================================================================")

    sys.exit(0 if summary["all_invariants_passed"] else 1)


if __name__ == "__main__":
    main()
