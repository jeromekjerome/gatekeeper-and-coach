from typing import Any, Dict, List, Optional
from backend.schemas import AttemptRecord, BadgeAward, EnablementReward

PRACTITIONER_LEVELS: List[Dict[str, Any]] = [
    {"name": "Novice Drafter", "min_xp": 0, "icon": "📘"},
    {"name": "Grounded Associate", "min_xp": 100, "icon": "⚖️"},
    {"name": "Compliance Practitioner", "min_xp": 250, "icon": "🛡️"},
    {"name": "Principal Gatekeeper", "min_xp": 500, "icon": "🏛️"},
    {"name": "Supervisory Principal", "min_xp": 850, "icon": "👑"},
]

MASTERY_BADGES: Dict[str, Dict[str, str]] = {
    "first_pass_compliant": {
        "id": "first_pass_compliant",
        "name": "First-Pass Compliant",
        "icon": "⚡",
        "description": "Passed 100% of Required and Prohibited criteria on Attempt #1 without auto-remediation.",
    },
    "prompt_architect": {
        "id": "prompt_architect",
        "name": "Prompt Architect",
        "icon": "🎯",
        "description": "Engineered an original practitioner prompt scoring 85/100 or higher.",
    },
    "citation_sentinel": {
        "id": "citation_sentinel",
        "name": "Citation Sentinel",
        "icon": "📚",
        "description": "Completed a legal drafting run with anchored record citations and zero unanchored hallucinations.",
    },
    "finra_guardian": {
        "id": "finra_guardian",
        "name": "FINRA 2210 Guardian",
        "icon": "🛡️",
        "description": "Passed a FINRA wealth advisory evaluation on Attempt #1 with balanced risk and fee disclosures.",
    },
    "streak_3": {
        "id": "streak_3",
        "name": "3x Zero-Violation Streak",
        "icon": "🔥",
        "description": "Achieved 3 consecutive First-Pass Compliant runs without triggering the retry router.",
    },
    "remediation_learner": {
        "id": "remediation_learner",
        "name": "Coach Apprentice",
        "icon": "🎓",
        "description": "Completed a root-cause coaching cycle after Attempt #1 was intercepted and remediated.",
    },
}


def get_level_metadata(total_xp: int) -> Dict[str, Any]:
    level_idx = 0
    for idx in range(len(PRACTITIONER_LEVELS) - 1, -1, -1):
        if total_xp >= PRACTITIONER_LEVELS[idx]["min_xp"]:
            level_idx = idx
            break

    current_level = PRACTITIONER_LEVELS[level_idx]
    next_level = (
        PRACTITIONER_LEVELS[level_idx + 1]
        if level_idx + 1 < len(PRACTITIONER_LEVELS)
        else None
    )

    if next_level:
        span = max(1, next_level["min_xp"] - current_level["min_xp"])
        progress_pct = min(
            100,
            max(0, round(((total_xp - current_level["min_xp"]) / span) * 100)),
        )
        xp_to_next = max(0, next_level["min_xp"] - total_xp)
        next_level_name: Optional[str] = next_level["name"]
    else:
        progress_pct = 100
        xp_to_next = 0
        next_level_name = None

    return {
        "level_name": current_level["name"],
        "level_icon": current_level["icon"],
        "progress_pct": progress_pct,
        "next_level_name": next_level_name,
        "xp_to_next_level": xp_to_next,
    }


def score_enablement_run(
    domain_id: str,
    attempts: List[AttemptRecord],
    current_xp: int = 0,
    current_streak: int = 0,
    unlocked_badges: Optional[List[str]] = None,
) -> EnablementReward:
    existing_badges = list(unlocked_badges or [])
    if not attempts:
        lvl = get_level_metadata(current_xp)
        return EnablementReward(
            xp_earned=0,
            streak_bonus_xp=0,
            total_xp=current_xp,
            first_pass_streak=current_streak,
            first_pass_compliant=False,
            level_name=lvl["level_name"],
            level_icon=lvl["level_icon"],
            progress_pct=lvl["progress_pct"],
            next_level_name=lvl["next_level_name"],
            xp_to_next_level=lvl["xp_to_next_level"],
            newly_unlocked_badges=[],
            all_badges=[
                BadgeAward(**MASTERY_BADGES[b])
                for b in existing_badges
                if b in MASTERY_BADGES
            ],
        )

    attempt_1 = attempts[0]
    final_attempt = attempts[-1]
    passed_first_try = attempt_1.evaluation.gate_status == "PASSED"
    final_passed = final_attempt.evaluation.gate_status == "PASSED"
    prompt_score = int(attempt_1.evaluation.enablement_coaching.prompt_quality_score)

    streak_bonus_xp = 0
    if passed_first_try:
        new_streak = current_streak + 1
        base_xp = 100 + int(prompt_score * 0.5)
        if new_streak >= 3:
            streak_bonus_xp = 50
        xp_earned = base_xp + streak_bonus_xp
    elif final_passed:
        new_streak = 0
        xp_earned = 35
    else:
        new_streak = 0
        xp_earned = 10

    new_total_xp = max(0, current_xp + xp_earned)

    candidate_badges: List[str] = []
    if passed_first_try:
        candidate_badges.append("first_pass_compliant")
        if domain_id == "finra_wealth_advisory":
            candidate_badges.append("finra_guardian")
    elif final_passed and len(attempts) > 1:
        candidate_badges.append("remediation_learner")

    if prompt_score >= 85:
        candidate_badges.append("prompt_architect")

    if new_streak >= 3:
        candidate_badges.append("streak_3")

    final_citations = getattr(final_attempt.evaluation, "citation_audit", None) or []
    if final_citations:
        has_anchored = any(c.status == "ANCHORED" for c in final_citations)
        has_unanchored = any(c.status == "UNANCHORED" for c in final_citations)
        if has_anchored and not has_unanchored and final_passed:
            candidate_badges.append("citation_sentinel")

    newly_unlocked: List[BadgeAward] = []
    for badge_id in candidate_badges:
        if badge_id not in existing_badges and badge_id in MASTERY_BADGES:
            existing_badges.append(badge_id)
            newly_unlocked.append(BadgeAward(**MASTERY_BADGES[badge_id]))

    all_badge_objs = [
        BadgeAward(**MASTERY_BADGES[b])
        for b in existing_badges
        if b in MASTERY_BADGES
    ]

    lvl = get_level_metadata(new_total_xp)
    return EnablementReward(
        xp_earned=xp_earned,
        streak_bonus_xp=streak_bonus_xp,
        total_xp=new_total_xp,
        first_pass_streak=new_streak,
        first_pass_compliant=passed_first_try,
        level_name=lvl["level_name"],
        level_icon=lvl["level_icon"],
        progress_pct=lvl["progress_pct"],
        next_level_name=lvl["next_level_name"],
        xp_to_next_level=lvl["xp_to_next_level"],
        newly_unlocked_badges=newly_unlocked,
        all_badges=all_badge_objs,
    )
