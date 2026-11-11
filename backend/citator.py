import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Literal, Optional, Tuple

from backend.schemas import CitationAuditItem, EvaluationReport, ProhibitedCheckResult

# Reporter pattern matching NY & Federal case reporters:
# e.g., 42 N.Y.2d 338, 142 A.D.3d 101, 100 N.Y.S.3d 200, 550 U.S. 544, 123 F.3d 456, 88 Misc. 3d 12
REPORTER_SPINE_REGEX = (
    r"\b\d{1,4}\s+"
    r"(?:N\.Y\.(?:2d|3d)?|A\.D\.(?:2d|3d)?|Misc\.\s*(?:2d|3d)?|N\.Y\.S\.(?:2d|3d)?|"
    r"U\.S\.|S\.\s*Ct\.|L\.\s*Ed\.\s*(?:2d)?|F\.(?:2d|3d|4th|Supp\.\s*(?:2d|3d)?))"
    r"\s+\d{1,5}\b"
)

# Full case citation: Party A v. Party B, 42 N.Y.2d 338 (1977)
FULL_CASE_CITATION_PATTERN = re.compile(
    r"(?:Matter\s+of\s+|In\s+re\s+)?"
    r"[A-Z][A-Za-z0-9&'.\-\s]{1,45}?\s+v\.?\s+[A-Z][A-Za-z0-9&'.,\-\s]{1,55}?,\s*"
    + REPORTER_SPINE_REGEX
    + r"(?:\s*,\s*\d{1,5})?"
    + r"(?:\s*\([^)]{2,35}\))?",
)

# Standalone reporter citation: 42 N.Y.2d 338
STANDALONE_REPORTER_PATTERN = re.compile(REPORTER_SPINE_REGEX)

# Standalone case name without reporter: e.g., Smith v. Jones
PARTY_V_PARTY_PATTERN = re.compile(
    r"\b(?:Matter\s+of\s+)?([A-Z][A-Za-z0-9&'.\-]+(?:\s+[A-Z][A-Za-z0-9&'.\-]+){0,3}"
    r"\s+v\.\s+"
    r"[A-Z][A-Za-z0-9&'.\-]+(?:\s+(?:[A-Z][A-Za-z0-9&'.\-]+|Assoc\.|Corp\.|Inc\.|LLC|Ltd\.|Co\.)){0,4})"
)

# Placeholder pattern permitted by PROH-LEG-01
PLACEHOLDER_PATTERN = re.compile(
    r"\[\s*(?:VERIFY\s+CITATION(?:\s+NEEDED)?|CITATION\s+NEEDED|VERIFY)\s*\]",
    re.IGNORECASE,
)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_matching(text: str) -> str:
    cleaned = text.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_citations_with_spans(draft_text: str) -> List[Tuple[str, int, int]]:
    """
    Deterministically extracts legal case citations and their character spans from draft_text.
    Orders and deduplicates spans so overlapping matches are consolidated cleanly.
    """
    spans: List[Tuple[str, int, int]] = []

    for match in FULL_CASE_CITATION_PATTERN.finditer(draft_text):
        cite = _normalize_whitespace(match.group(0))
        spans.append((cite, match.start(), match.end()))

    for match in STANDALONE_REPORTER_PATTERN.finditer(draft_text):
        start, end = match.start(), match.end()
        if any(s <= start and end <= e for _, s, e in spans):
            continue
        cite = _normalize_whitespace(match.group(0))
        spans.append((cite, start, end))

    for match in PARTY_V_PARTY_PATTERN.finditer(draft_text):
        start, end = match.start(1), match.end(1)
        if any(not (end <= s or start >= e) for _, s, e in spans):
            continue
        cite = _normalize_whitespace(match.group(1)).rstrip(".,;:")
        spans.append((cite, start, end))

    spans.sort(key=lambda item: item[1])

    # Deduplicate by normalized citation text while preserving first appearance order
    unique_results: List[Tuple[str, int, int]] = []
    seen = set()
    for cite, start, end in spans:
        norm = _normalize_for_matching(cite)
        if norm and norm not in seen:
            seen.add(norm)
            unique_results.append((cite, start, end))

    return unique_results


def is_citation_anchored_in_source(citation_text: str, source_notes: str) -> bool:
    """
    Deterministically checks whether a citation extracted from the draft is anchored
    in the practitioner's verified source_notes packet.
    """
    if not source_notes.strip():
        return False

    norm_source = _normalize_for_matching(source_notes)

    # 1. If the citation contains a reporter spine (e.g. "42 N.Y.2d 338"), check if that spine is in source_notes
    spine_match = STANDALONE_REPORTER_PATTERN.search(citation_text)
    if spine_match:
        norm_spine = _normalize_for_matching(spine_match.group(0))
        if norm_spine and norm_spine in norm_source:
            return True
        return False

    # 2. Otherwise check if the normalized party-v-party name appears in source_notes
    party_match = PARTY_V_PARTY_PATTERN.search(citation_text)
    if party_match:
        norm_party = _normalize_for_matching(party_match.group(1))
        if norm_party and norm_party in norm_source:
            return True

    norm_cite = _normalize_for_matching(citation_text)
    return bool(norm_cite and norm_cite in norm_source)


def is_flagged_with_placeholder(draft_text: str, start: int, end: int) -> bool:
    """
    Checks whether the citation in draft_text is accompanied by a '[VERIFY CITATION Needed]'
    placeholder within the surrounding clause/sentence window.
    """
    window_start = max(0, start - 40)
    window_end = min(len(draft_text), end + 85)
    snippet = draft_text[window_start:window_end]
    return bool(PLACEHOLDER_PATTERN.search(snippet))


def _is_courtlistener_configured() -> Optional[str]:
    token = (
        os.environ.get("COURTLISTENER_TOKEN")
        or os.environ.get("COURTLISTENER_API_KEY")
        or ""
    ).strip()
    if not token:
        return None
    if token.lower().startswith(("your_", "optional_", "replace_", "insert_")):
        return None
    return token


def verify_citation_courtlistener(
    citation_text: str,
) -> Tuple[Literal["VERIFIED", "NOT_FOUND_404", "UNCHECKED"], Optional[str]]:
    """
    Calls the CourtListener v4 citation-lookup endpoint when COURTLISTENER_TOKEN is configured.
    Returns (courtlistener_status, courtlistener_url).
    """
    token = _is_courtlistener_configured()
    if not token:
        return ("UNCHECKED", None)

    url = "https://www.courtlistener.com/api/rest/v4/citation-lookup/"
    form_data = urllib.parse.urlencode({"text": citation_text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=form_data,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "GatekeeperAndCoach-Citator/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, list) and len(payload) > 0:
            first = payload[0]
            status_code = first.get("status")
            clusters = first.get("clusters") or []
            if status_code == 200 and len(clusters) > 0:
                abs_url = clusters[0].get("absolute_url")
                canonical_url = (
                    f"https://www.courtlistener.com{abs_url}"
                    if abs_url and abs_url.startswith("/")
                    else abs_url
                )
                return ("VERIFIED", canonical_url)
            return ("NOT_FOUND_404", None)
        return ("NOT_FOUND_404", None)
    except urllib.error.HTTPError as http_err:
        if http_err.code == 404:
            return ("NOT_FOUND_404", None)
        return ("UNCHECKED", None)
    except Exception:
        return ("UNCHECKED", None)


def audit_draft_citations(
    draft_text: str,
    source_notes: str,
) -> List[CitationAuditItem]:
    """
    Runs the deterministic citation parser, source-notes anchoring check, placeholder detector,
    and optional CourtListener v4 lookup across all case citations found in draft_text.
    """
    extracted = extract_citations_with_spans(draft_text)
    audit_items: List[CitationAuditItem] = []

    for cite_text, start, end in extracted:
        if is_citation_anchored_in_source(cite_text, source_notes):
            anchor_status: Literal["ANCHORED", "PLACEHOLDER_FLAGGED", "UNANCHORED"] = (
                "ANCHORED"
            )
        elif is_flagged_with_placeholder(draft_text, start, end):
            anchor_status = "PLACEHOLDER_FLAGGED"
        else:
            anchor_status = "UNANCHORED"

        cl_status, cl_url = verify_citation_courtlistener(cite_text)

        audit_items.append(
            CitationAuditItem(
                citation_text=cite_text,
                status=anchor_status,
                courtlistener_status=cl_status,
                courtlistener_url=cl_url,
            )
        )

    return audit_items


def apply_deterministic_citation_gate(
    report: EvaluationReport,
    draft_text: str,
    source_notes: str,
    rubric: Optional[dict] = None,
) -> EvaluationReport:
    """
    Integrates the deterministic citation verifier with the evaluation report:
    1. Populates report.citation_audit whenever the domain rubric includes PROH-LEG-01
       (or ny_litigation_drafting).
    2. If ANY citation in report.citation_audit has status == 'UNANCHORED' or
       courtlistener_status == 'NOT_FOUND_404', deterministically forces PROH-LEG-01
       to violated = True and sets gate_status = 'BLOCKED'.
    """
    if rubric is not None:
        prohibited_ids = {
            item.get("id") for item in (rubric.get("prohibited_criteria") or [])
        }
        is_legal_domain = (
            "PROH-LEG-01" in prohibited_ids
            or rubric.get("domain_id") == "ny_litigation_drafting"
        )
    else:
        is_legal_domain = any(
            c.id == "PROH-LEG-01" for c in (report.prohibited_checks or [])
        )

    if not is_legal_domain:
        return report

    citation_items = audit_draft_citations(
        draft_text=draft_text, source_notes=source_notes
    )
    report.citation_audit = citation_items

    unanchored_items = [
        item
        for item in citation_items
        if item.status == "UNANCHORED" or item.courtlistener_status == "NOT_FOUND_404"
    ]

    if not unanchored_items:
        return report

    offending_list = "; ".join(
        f"{item.citation_text} [{item.status}"
        + (
            f" / CourtListener: {item.courtlistener_status}]"
            if item.courtlistener_status != "UNCHECKED"
            else "]"
        )
        for item in unanchored_items
    )
    remediation_msg = (
        "Deterministic Citator blocked unanchored case authority: "
        f"{offending_list}. Remove all case citations not explicitly supplied in the "
        "verified source_notes packet, or append '[VERIFY CITATION Needed]'."
    )

    # Locate PROH-LEG-01 in report.prohibited_checks and deterministically force violated = True
    found_proh_leg_01 = False
    for check in report.prohibited_checks:
        if check.id == "PROH-LEG-01":
            found_proh_leg_01 = True
            check.violated = True
            check.offending_quote = offending_list
            check.remediation_instruction = remediation_msg
            break

    if not found_proh_leg_01:
        report.prohibited_checks.append(
            ProhibitedCheckResult(
                id="PROH-LEG-01",
                name="Unanchored Case Citations (Hallucination Gate)",
                violated=True,
                offending_quote=offending_list,
                remediation_instruction=remediation_msg,
            )
        )

    report.gate_status = "BLOCKED"
    return report


enforce_deterministic_citation_gate = apply_deterministic_citation_gate
