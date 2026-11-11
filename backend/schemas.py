from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class CitationAuditItem(BaseModel):
    citation_text: str = Field(
        description="Extracted case or reporter citation from the draft."
    )
    status: Literal["ANCHORED", "PLACEHOLDER_FLAGGED", "UNANCHORED"] = Field(
        description="Whether the citation is anchored in source_notes, flagged with placeholder, or unanchored."
    )
    courtlistener_status: Literal["VERIFIED", "NOT_FOUND_404", "UNCHECKED"] = Field(
        default="UNCHECKED",
        description="Live CourtListener citation-lookup status when COURTLISTENER_TOKEN is configured.",
    )
    courtlistener_url: Optional[str] = Field(
        default=None,
        description="Canonical CourtListener URL when verified.",
    )


class RequiredCheckResult(BaseModel):
    id: str = Field(description="Rule ID from the JSON rubric, e.g., REQ-FIN-01")
    name: str
    passed: bool
    evidence_or_gap: str = Field(
        description="Exact quote proving compliance, or specific explanation of what is missing."
    )


class ProhibitedCheckResult(BaseModel):
    id: str = Field(description="Rule ID from the JSON rubric, e.g., PROH-FIN-01")
    name: str
    violated: bool = Field(description="True if the draft violated this prohibition.")
    offending_quote: str = Field(
        description="Verbatim quote from the draft that violated the rule, or 'None' if clean."
    )
    remediation_instruction: str = Field(
        description="Specific instruction for the retry loop to fix this violation."
    )


class EnablementCoaching(BaseModel):
    prompt_quality_score: int = Field(ge=0, le=100)
    why_your_prompt_caused_this: str = Field(
        description="Plain-English explanation for a non-engineer (attorney or financial advisor) explaining how their prompt phrasing triggered model sycophancy, omission, or hallucination."
    )
    upgraded_prompt_template: str = Field(
        description="A reusable, well-structured prompt the practitioner should use next time."
    )
    human_verification_checklist: List[str] = Field(
        description="2 to 3 specific factual or regulatory items the human professional must verify before signing off."
    )


class EvaluationReport(BaseModel):
    gate_status: Literal["PASSED", "BLOCKED"]
    required_checks: List[RequiredCheckResult]
    prohibited_checks: List[ProhibitedCheckResult]
    enablement_coaching: EnablementCoaching
    citation_audit: List[CitationAuditItem] = Field(
        default_factory=list,
        description="Deterministic citation audit trail populated by backend/citator.py.",
    )


class AttemptRecord(BaseModel):
    attempt_number: int
    draft_text: str
    evaluation: EvaluationReport


class BadgeAward(BaseModel):
    id: str
    name: str
    icon: str
    description: str


class EnablementReward(BaseModel):
    xp_earned: int
    streak_bonus_xp: int = 0
    total_xp: int
    first_pass_streak: int
    first_pass_compliant: bool
    level_name: str
    level_icon: str
    progress_pct: int
    next_level_name: Optional[str] = None
    xp_to_next_level: int = 0
    newly_unlocked_badges: List[BadgeAward] = Field(default_factory=list)
    all_badges: List[BadgeAward] = Field(default_factory=list)


class PipelineResponse(BaseModel):
    domain_id: str
    final_status: Literal["PASSED", "BLOCKED"]
    attempts: List[AttemptRecord]
    enablement_reward: Optional[EnablementReward] = None


class PipelineRequest(BaseModel):
    domain_id: str = Field(
        description="Target domain ID matching a rubric in backend/rubrics/*.json"
    )
    source_notes: str = Field(
        description="Ground-truth source notes or legal record packet"
    )
    practitioner_prompt: str = Field(
        description="The practitioner's prompt instructing the model"
    )
    model_id: Optional[str] = Field(
        default="gemini-2.5-flash",
        description="Selected model ID (e.g., gemini-2.5-flash, gemini-2.5-pro, claude-sonnet, gpt-4.1, o3-mini)",
    )
    current_xp: int = Field(
        default=0,
        ge=0,
        description="Practitioner's accumulated XP prior to this run",
    )
    current_streak: int = Field(
        default=0,
        ge=0,
        description="Practitioner's consecutive First-Pass Compliant streak",
    )
    unlocked_badges: List[str] = Field(
        default_factory=list,
        description="Badge IDs already unlocked by the practitioner",
    )
