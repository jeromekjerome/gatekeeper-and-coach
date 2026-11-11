/**
 * Gatekeeper & Coach — Enterprise AI Enablement Sandbox
 * Dependency-free Vanilla JS Frontend Controller
 *
 * Core Invariants Enforced:
 * 1. Strict Gatekeeping: The "Unlock & Export Final Document" button (#exportDocBtn)
 *    MUST remain disabled (`disabled = true`) until:
 *      (a) final_status === "PASSED" (all Required & Prohibited criteria passed on the latest attempt)
 *      (b) 100% of checkboxes in enablement_coaching.human_verification_checklist are manually checked.
 * 2. Auditability: Every attempt (Attempt 1 blocked draft in red alongside Attempt 2 remediated draft
 *    in green) is rendered with its full Required and Prohibited criteria evidence scorecard.
 * 3. No Silent Fixes: Even when Attempt 2 succeeds after auto-remediation, the Enablement Coach panel
 *    explains what flaw in the practitioner's original prompt caused Attempt 1 to fail.
 */

const MASTERY_STORAGE_KEY = "gatekeeper_coach_mastery_v1";

const state = {
  domains: [],
  scenarios: {},
  models: [],
  gamificationCatalog: {
    levels: [
      { name: "Novice Drafter", min_xp: 0, icon: "📘" },
      { name: "Grounded Associate", min_xp: 100, icon: "⚖️" },
      { name: "Compliance Practitioner", min_xp: 250, icon: "🛡️" },
      { name: "Principal Gatekeeper", min_xp: 500, icon: "🏛️" },
      { name: "Supervisory Principal", min_xp: 850, icon: "👑" },
    ],
    badges: [
      {
        id: "first_pass_compliant",
        name: "First-Pass Compliant",
        icon: "⚡",
        description: "Passed 100% of Required and Prohibited criteria on Attempt #1 without auto-remediation.",
      },
      {
        id: "prompt_architect",
        name: "Prompt Architect",
        icon: "🎯",
        description: "Engineered an original practitioner prompt scoring 85/100 or higher.",
      },
      {
        id: "citation_sentinel",
        name: "Citation Sentinel",
        icon: "📚",
        description: "Completed a legal drafting run with anchored record citations and zero unanchored hallucinations.",
      },
      {
        id: "finra_guardian",
        name: "FINRA 2210 Guardian",
        icon: "🛡️",
        description: "Passed a FINRA wealth advisory evaluation on Attempt #1 with balanced risk and fee disclosures.",
      },
      {
        id: "streak_3",
        name: "3x Zero-Violation Streak",
        icon: "🔥",
        description: "Achieved 3 consecutive First-Pass Compliant runs without triggering the retry router.",
      },
      {
        id: "remediation_learner",
        name: "Coach Apprentice",
        icon: "🎓",
        description: "Completed a root-cause coaching cycle after Attempt #1 was intercepted and remediated.",
      },
    ],
  },
  mastery: {
    total_xp: 0,
    first_pass_streak: 0,
    unlocked_badges: [],
    last_xp_earned: 0,
    newly_unlocked_ids: [],
  },
  activeDomainId: "",
  latestPipelineResponse: null,
  hitlCheckedMap: {}, // index -> boolean
};

// DOM Elements
const domainSelect = document.getElementById("domainSelect");
const modelSelect = document.getElementById("modelSelect");
const loadFinraScenarioBtn = document.getElementById("loadFinraScenarioBtn");
const loadLegalScenarioBtn = document.getElementById("loadLegalScenarioBtn");

const activeDomainBadge = document.getElementById("activeDomainBadge");
const sourceNotesInput = document.getElementById("sourceNotesInput");
const practitionerPromptInput = document.getElementById("practitionerPromptInput");
const runPipelineBtn = document.getElementById("runPipelineBtn");
const runBtnText = document.getElementById("runBtnText");
const rubricRetryMeta = document.getElementById("rubricRetryMeta");
const rubricCriteriaContainer = document.getElementById("rubricCriteriaContainer");

const pipelineStatusBadge = document.getElementById("pipelineStatusBadge");
const auditEmptyState = document.getElementById("auditEmptyState");
const auditLoadingState = document.getElementById("auditLoadingState");
const auditErrorBanner = document.getElementById("auditErrorBanner");
const auditResultsContainer = document.getElementById("auditResultsContainer");
const pipelineSummaryBanner = document.getElementById("pipelineSummaryBanner");
const attemptsComparisonGrid = document.getElementById("attemptsComparisonGrid");

const promptScoreBadge = document.getElementById("promptScoreBadge");
const coachEmptyState = document.getElementById("coachEmptyState");
const coachContentContainer = document.getElementById("coachContentContainer");
const coachScoreValue = document.getElementById("coachScoreValue");
const coachScoreBar = document.getElementById("coachScoreBar");
const coachWhyText = document.getElementById("coachWhyText");
const coachUpgradedPrompt = document.getElementById("coachUpgradedPrompt");
const applyUpgradedPromptBtn = document.getElementById("applyUpgradedPromptBtn");
const hitlProgressBadge = document.getElementById("hitlProgressBadge");
const hitlChecklistContainer = document.getElementById("hitlChecklistContainer");

const masteryLevelIcon = document.getElementById("masteryLevelIcon");
const masteryLevelName = document.getElementById("masteryLevelName");
const masteryStreakPill = document.getElementById("masteryStreakPill");
const masteryStreakCount = document.getElementById("masteryStreakCount");
const masteryTotalXpPill = document.getElementById("masteryTotalXpPill");
const masteryXpBarFill = document.getElementById("masteryXpBarFill");
const masteryNextLevelHint = document.getElementById("masteryNextLevelHint");
const masteryLastGainBadge = document.getElementById("masteryLastGainBadge");
const masteryBadgeCount = document.getElementById("masteryBadgeCount");
const masteryBadgesGrid = document.getElementById("masteryBadgesGrid");
const resetMasteryBtn = document.getElementById("resetMasteryBtn");

const gateReqCompliance = document.getElementById("gateReqCompliance");
const gateReqComplianceIcon = document.getElementById("gateReqComplianceIcon");
const gateReqHitl = document.getElementById("gateReqHitl");
const gateReqHitlIcon = document.getElementById("gateReqHitlIcon");
const exportDocBtn = document.getElementById("exportDocBtn");

const exportModal = document.getElementById("exportModal");
const closeExportModalBtn = document.getElementById("closeExportModalBtn");
const exportDocumentPreview = document.getElementById("exportDocumentPreview");
const copyExportBtn = document.getElementById("copyExportBtn");
const downloadExportBtn = document.getElementById("downloadExportBtn");

/**
 * Initialize Workbench Configuration from Backend
 */
async function initWorkbench() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) {
      throw new Error(`Failed to load configuration (HTTP ${res.status})`);
    }
    const config = await res.json();
    state.domains = config.domains || [];
    state.scenarios = config.scenarios || {};
    state.models = config.models || [];
    if (config.gamification) {
      state.gamificationCatalog = config.gamification;
    }

    loadMasteryState();
    renderMasteryCard();
    renderDomainOptions();
    renderModelOptions();

    // Load default FINRA scenario on initial boot for immediate zero-friction evaluation
    if (state.scenarios.finra_scenario) {
      applyScenario(state.scenarios.finra_scenario);
    } else if (state.domains.length > 0) {
      setActiveDomain(state.domains[0].domain_id);
    }

    enforceStrictExportGate();
  } catch (err) {
    showErrorBanner(`Initialization error: ${err.message}`);
  }
}

function renderDomainOptions() {
  domainSelect.innerHTML = "";
  state.domains.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d.domain_id;
    opt.textContent = d.domain_name;
    domainSelect.appendChild(opt);
  });
}

function renderModelOptions() {
  modelSelect.innerHTML = "";
  state.models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.id;
    const statusSuffix = m.available ? "" : " (Key not in .env — routes via Gemini)";
    opt.textContent = `${m.label}${statusSuffix}`;
    modelSelect.appendChild(opt);
  });
}

function setActiveDomain(domainId) {
  state.activeDomainId = domainId;
  domainSelect.value = domainId;

  const rubric = state.domains.find((d) => d.domain_id === domainId);
  if (!rubric) return;

  activeDomainBadge.textContent = rubric.domain_id;
  rubricRetryMeta.textContent = `Max Auto-Retries: ${rubric.max_auto_retries ?? 2}`;

  rubricCriteriaContainer.innerHTML = "";

  // Render Required Criteria
  (rubric.required_criteria || []).forEach((req) => {
    const item = document.createElement("div");
    item.className = "rubric-item req-rule";
    item.innerHTML = `
      <div class="rubric-item-top">
        <span class="rule-code">${escapeHtml(req.id)}</span>
        <span>${escapeHtml(req.name)}</span>
      </div>
      <div class="rubric-item-desc">${escapeHtml(req.description)}</div>
    `;
    rubricCriteriaContainer.appendChild(item);
  });

  // Render Prohibited Criteria
  (rubric.prohibited_criteria || []).forEach((proh) => {
    const item = document.createElement("div");
    item.className = "rubric-item proh-rule";
    item.innerHTML = `
      <div class="rubric-item-top">
        <span class="rule-code">${escapeHtml(proh.id)}</span>
        <span>${escapeHtml(proh.name)}</span>
      </div>
      <div class="rubric-item-desc">${escapeHtml(proh.description)}</div>
    `;
    rubricCriteriaContainer.appendChild(item);
  });
}

function applyScenario(scenario) {
  if (!scenario) return;
  setActiveDomain(scenario.domain_id);
  sourceNotesInput.value = scenario.source_notes || "";
  practitionerPromptInput.value = scenario.flawed_prompt || "";
  resetPipelineResults();
}

function resetPipelineResults() {
  state.latestPipelineResponse = null;
  state.hitlCheckedMap = {};

  pipelineStatusBadge.className = "badge badge-idle";
  pipelineStatusBadge.textContent = "AWAITING RUN";
  promptScoreBadge.className = "badge badge-neutral";
  promptScoreBadge.textContent = "Prompt Score: --";

  auditErrorBanner.classList.add("hidden");
  auditLoadingState.classList.add("hidden");
  auditResultsContainer.classList.add("hidden");
  auditEmptyState.classList.remove("hidden");

  coachContentContainer.classList.add("hidden");
  coachEmptyState.classList.remove("hidden");

  enforceStrictExportGate();
}

/**
 * Execute the Gatekeeper & Coach Pipeline via POST /api/evaluate
 */
async function runGatekeeperLoop() {
  const domainId = domainSelect.value;
  const modelId = modelSelect.value;
  const sourceNotes = sourceNotesInput.value.trim();
  const practitionerPrompt = practitionerPromptInput.value.trim();

  if (!sourceNotes || !practitionerPrompt) {
    showErrorBanner("Please provide both Verified Source Notes and a Practitioner Prompt.");
    return;
  }

  // Enter Loading State
  runPipelineBtn.disabled = true;
  runBtnText.textContent = "Evaluating & Remediating...";
  auditErrorBanner.classList.add("hidden");
  auditEmptyState.classList.add("hidden");
  auditResultsContainer.classList.add("hidden");
  auditLoadingState.classList.remove("hidden");

  pipelineStatusBadge.className = "badge badge-neutral";
  pipelineStatusBadge.textContent = "RUNNING GATEKEEPER...";

  // Reset export gate during run
  state.latestPipelineResponse = null;
  state.hitlCheckedMap = {};
  enforceStrictExportGate();

  try {
    const response = await fetch("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain_id: domainId,
        source_notes: sourceNotes,
        practitioner_prompt: practitionerPrompt,
        model_id: modelId,
        current_xp: state.mastery.total_xp || 0,
        current_streak: state.mastery.first_pass_streak || 0,
        unlocked_badges: state.mastery.unlocked_badges || [],
      }),
    });

    if (!response.ok) {
      const errPayload = await response.json().catch(() => ({}));
      throw new Error(errPayload.detail || `Server returned HTTP ${response.status}`);
    }

    const pipelineData = await response.json();
    state.latestPipelineResponse = pipelineData;

    renderPipelineResults(pipelineData);
  } catch (err) {
    auditLoadingState.classList.add("hidden");
    showErrorBanner(err.message);
    pipelineStatusBadge.className = "badge badge-blocked";
    pipelineStatusBadge.textContent = "ERROR";
  } finally {
    runPipelineBtn.disabled = false;
    runBtnText.textContent = "Run Gatekeeper & Auto-Remediation Loop";
  }
}

/**
 * Render Audit Trail (All Intermediate Attempts + Final Status) and Enablement Coaching
 */
function renderPipelineResults(pipelineData) {
  auditLoadingState.classList.add("hidden");
  auditResultsContainer.classList.remove("hidden");

  const attempts = pipelineData.attempts || [];
  const finalStatus = pipelineData.final_status;

  // Update Header Status Badge
  if (finalStatus === "PASSED") {
    pipelineStatusBadge.className = "badge badge-passed";
    pipelineStatusBadge.textContent = `PASSED (${attempts.length} ATTEMPT${attempts.length > 1 ? "S" : ""})`;
  } else {
    pipelineStatusBadge.className = "badge badge-blocked";
    pipelineStatusBadge.textContent = `BLOCKED (${attempts.length} ATTEMPT${attempts.length > 1 ? "S" : ""})`;
  }

  // Update Summary Banner
  const firstAttemptBlocked = attempts.length > 0 && attempts[0].evaluation.gate_status === "BLOCKED";
  if (finalStatus === "PASSED" && firstAttemptBlocked) {
    pipelineSummaryBanner.className = "pipeline-summary-banner summary-passed";
    pipelineSummaryBanner.innerHTML = `
      <span>&#9989; <strong>Gatekeeper Intercepted &amp; Remediated:</strong> Attempt 1 was BLOCKED for compliance violations; Auto-Retry Attempt ${attempts.length} PASSED 100% of criteria.</span>
      <span>Review Coaching &amp; Complete HITL Checklist &rarr;</span>
    `;
  } else if (finalStatus === "PASSED") {
    pipelineSummaryBanner.className = "pipeline-summary-banner summary-passed";
    pipelineSummaryBanner.innerHTML = `
      <span>&#9989; <strong>Passed on Attempt 1:</strong> Draft satisfied 100% of Required and Prohibited criteria on the first pass.</span>
      <span>Complete HITL Checklist to Export &rarr;</span>
    `;
  } else {
    pipelineSummaryBanner.className = "pipeline-summary-banner summary-blocked";
    pipelineSummaryBanner.innerHTML = `
      <span>&#10060; <strong>Gatekeeper BLOCKED Export:</strong> Draft remained non-compliant after ${attempts.length} attempt(s). Export stays locked.</span>
    `;
  }

  // Render Every Attempt Card (Attempt 1 in Red if blocked, Attempt 2 in Green if passed)
  attemptsComparisonGrid.innerHTML = "";
  attempts.forEach((attempt) => {
    const isPassed = attempt.evaluation.gate_status === "PASSED";
    const card = document.createElement("article");
    card.className = `attempt-card ${isPassed ? "attempt-passed" : "attempt-blocked"}`;

    const roleDescription =
      attempt.attempt_number === 1
        ? isPassed
          ? "Initial Practitioner Draft (Compliant)"
          : "Initial Practitioner Draft — Intercepted & Blocked"
        : isPassed
        ? "Auto-Remediated Draft — Compliant & Anchored to Record"
        : `Auto-Remediation Attempt ${attempt.attempt_number} — Still Blocked`;

    const reqRowsHtml = (attempt.evaluation.required_checks || [])
      .map(
        (req) => `
        <div class="check-row ${req.passed ? "check-pass" : "check-fail"}">
          <div class="check-row-header">
            <span><span class="rule-code">${escapeHtml(req.id)}</span> ${escapeHtml(req.name)}</span>
            <span class="check-status-pill ${req.passed ? "pill-pass" : "pill-fail"}">
              ${req.passed ? "PASSED" : "MISSING"}
            </span>
          </div>
          <div class="check-detail">
            <strong>${req.passed ? "Evidence:" : "Compliance Gap:"}</strong>
            ${escapeHtml(req.evidence_or_gap)}
          </div>
        </div>
      `
      )
      .join("");

    const prohRowsHtml = (attempt.evaluation.prohibited_checks || [])
      .map(
        (proh) => `
        <div class="check-row ${!proh.violated ? "check-pass" : "check-fail"}">
          <div class="check-row-header">
            <span><span class="rule-code">${escapeHtml(proh.id)}</span> ${escapeHtml(proh.name)}</span>
            <span class="check-status-pill ${!proh.violated ? "pill-pass" : "pill-fail"}">
              ${!proh.violated ? "CLEAN" : "VIOLATED"}
            </span>
          </div>
          <div class="check-detail">
            <strong>Offending Quote:</strong> "${escapeHtml(proh.offending_quote)}"
          </div>
          ${
            proh.violated
              ? `<div class="check-remediation"><strong>Remediation Router Instruction:</strong> ${escapeHtml(
                  proh.remediation_instruction
                )}</div>`
              : ""
          }
        </div>
      `
      )
      .join("");

    const citationItems = attempt.evaluation.citation_audit || [];
    const citationSectionHtml =
      citationItems.length > 0
        ? `
        <div class="citation-audit-section">
          <div class="citation-audit-header">
            <h4>&#9878; Deterministic Citation &amp; Authority Verifier (backend/citator.py)</h4>
            <span class="check-status-pill ${
              citationItems.some(
                (c) => c.status === "UNANCHORED" || c.courtlistener_status === "NOT_FOUND_404"
              )
                ? "pill-fail"
                : "pill-pass"
            }">
              ${
                citationItems.filter((c) => c.status === "ANCHORED").length
              }/${citationItems.length} Anchored in Record
            </span>
          </div>
          <div class="citation-audit-list">
            ${citationItems
              .map((cite) => {
                const rowClass =
                  cite.status === "ANCHORED" && cite.courtlistener_status !== "NOT_FOUND_404"
                    ? "cite-anchored"
                    : cite.status === "PLACEHOLDER_FLAGGED" &&
                      cite.courtlistener_status !== "NOT_FOUND_404"
                    ? "cite-placeholder"
                    : "cite-unanchored";

                const statusPillClass =
                  cite.status === "ANCHORED"
                    ? "pill-pass"
                    : cite.status === "PLACEHOLDER_FLAGGED"
                    ? "pill-warn"
                    : "pill-fail";

                let clBadgeHtml = "";
                if (cite.courtlistener_status === "VERIFIED") {
                  clBadgeHtml = cite.courtlistener_url
                    ? `<a href="${escapeHtml(
                        cite.courtlistener_url
                      )}" target="_blank" rel="noopener noreferrer" class="check-status-pill pill-pass cl-link">CourtListener: VERIFIED &#8599;</a>`
                    : `<span class="check-status-pill pill-pass">CourtListener: VERIFIED</span>`;
                } else if (cite.courtlistener_status === "NOT_FOUND_404") {
                  clBadgeHtml = `<span class="check-status-pill pill-fail">CourtListener: 404 NOT FOUND</span>`;
                } else {
                  clBadgeHtml = `<span class="check-status-pill pill-neutral">CourtListener: UNCHECKED</span>`;
                }

                return `
                  <div class="citation-audit-row ${rowClass}">
                    <span class="citation-text-code">${escapeHtml(cite.citation_text)}</span>
                    <div class="citation-badges">
                      <span class="check-status-pill ${statusPillClass}">Record: ${escapeHtml(
                  cite.status
                )}</span>
                      ${clBadgeHtml}
                    </div>
                  </div>
                `;
              })
              .join("")}
          </div>
        </div>
      `
        : "";

    card.innerHTML = `
      <div class="attempt-card-header">
        <div class="attempt-title">
          <span>Attempt #${attempt.attempt_number}: ${escapeHtml(roleDescription)}</span>
        </div>
        <span class="badge ${isPassed ? "badge-passed" : "badge-blocked"}">
          GATE: ${escapeHtml(attempt.evaluation.gate_status)}
        </span>
      </div>
      <div class="attempt-body">
        <div>
          <div class="draft-box-label">Generated Document Text (Attempt #${attempt.attempt_number})</div>
          <div class="draft-text-box">${escapeHtml(attempt.draft_text)}</div>
        </div>

        ${citationSectionHtml}

        <div class="criteria-matrix">
          <div class="criteria-column">
            <h4>Required Criteria (${(attempt.evaluation.required_checks || []).filter((c) => c.passed).length}/${
      (attempt.evaluation.required_checks || []).length
    } Passed)</h4>
            ${reqRowsHtml}
          </div>
          <div class="criteria-column">
            <h4>Prohibited Criteria (${
              (attempt.evaluation.prohibited_checks || []).filter((c) => c.violated).length
            } Violations)</h4>
            ${prohRowsHtml}
          </div>
        </div>
      </div>
    `;

    attemptsComparisonGrid.appendChild(card);
  });

  // Render Enablement Coaching from the latest attempt (which preserves Attempt 1's root-cause diagnosis)
  const latestAttempt = attempts[attempts.length - 1];
  if (latestAttempt && latestAttempt.evaluation && latestAttempt.evaluation.enablement_coaching) {
    renderEnablementCoach(latestAttempt.evaluation.enablement_coaching);
  }

  // Update Practitioner Enablement Gamification & Mastery Card
  if (pipelineData.enablement_reward) {
    applyEnablementReward(pipelineData.enablement_reward);
  }

  enforceStrictExportGate();
}

function loadMasteryState() {
  try {
    const raw = localStorage.getItem(MASTERY_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.mastery.total_xp = Number(parsed.total_xp || 0);
    state.mastery.first_pass_streak = Number(parsed.first_pass_streak || 0);
    state.mastery.unlocked_badges = Array.isArray(parsed.unlocked_badges)
      ? parsed.unlocked_badges
      : [];
    state.mastery.last_xp_earned = 0;
    state.mastery.newly_unlocked_ids = [];
  } catch (_) {
    // Ignore storage parse errors
  }
}

function saveMasteryState() {
  try {
    localStorage.setItem(
      MASTERY_STORAGE_KEY,
      JSON.stringify({
        total_xp: state.mastery.total_xp,
        first_pass_streak: state.mastery.first_pass_streak,
        unlocked_badges: state.mastery.unlocked_badges,
      })
    );
  } catch (_) {
    // Ignore storage quota errors
  }
}

function computeClientLevelMetadata(totalXp) {
  const levels = state.gamificationCatalog.levels || [];
  if (!levels.length) {
    return {
      level_name: "Novice Drafter",
      level_icon: "📘",
      progress_pct: 0,
      next_level_name: "Grounded Associate",
      xp_to_next_level: 100,
    };
  }
  let idx = 0;
  for (let i = levels.length - 1; i >= 0; i--) {
    if (totalXp >= levels[i].min_xp) {
      idx = i;
      break;
    }
  }
  const cur = levels[idx];
  const next = idx + 1 < levels.length ? levels[idx + 1] : null;
  if (next) {
    const span = Math.max(1, next.min_xp - cur.min_xp);
    const pct = Math.min(100, Math.max(0, Math.round(((totalXp - cur.min_xp) / span) * 100)));
    return {
      level_name: cur.name,
      level_icon: cur.icon,
      progress_pct: pct,
      next_level_name: next.name,
      xp_to_next_level: Math.max(0, next.min_xp - totalXp),
    };
  }
  return {
    level_name: cur.name,
    level_icon: cur.icon,
    progress_pct: 100,
    next_level_name: null,
    xp_to_next_level: 0,
  };
}

function applyEnablementReward(reward) {
  state.mastery.total_xp = Number(reward.total_xp ?? state.mastery.total_xp);
  state.mastery.first_pass_streak = Number(reward.first_pass_streak ?? 0);
  state.mastery.last_xp_earned = Number(reward.xp_earned ?? 0);
  state.mastery.newly_unlocked_ids = (reward.newly_unlocked_badges || []).map((b) => b.id);
  state.mastery.unlocked_badges = (reward.all_badges || []).map((b) => b.id);

  saveMasteryState();
  renderMasteryCard(reward);
}

function renderMasteryCard(rewardOverride = null) {
  const totalXp = state.mastery.total_xp || 0;
  const streak = state.mastery.first_pass_streak || 0;
  const lvl = rewardOverride || computeClientLevelMetadata(totalXp);

  if (masteryLevelIcon) masteryLevelIcon.textContent = lvl.level_icon || "📘";
  if (masteryLevelName) masteryLevelName.textContent = lvl.level_name || "Novice Drafter";
  if (masteryStreakCount) masteryStreakCount.textContent = String(streak);
  if (masteryStreakPill) {
    masteryStreakPill.classList.toggle("streak-hot", streak > 0);
  }
  if (masteryTotalXpPill) masteryTotalXpPill.textContent = `${totalXp} XP`;
  if (masteryXpBarFill) {
    masteryXpBarFill.style.width = `${Math.min(100, Math.max(0, lvl.progress_pct ?? 0))}%`;
  }
  if (masteryNextLevelHint) {
    masteryNextLevelHint.textContent = lvl.next_level_name
      ? `${lvl.xp_to_next_level} XP to ${lvl.next_level_name}`
      : "Max Supervisory Rank Achieved";
  }

  if (masteryLastGainBadge) {
    if (state.mastery.last_xp_earned > 0) {
      const bonusSuffix =
        rewardOverride && rewardOverride.streak_bonus_xp > 0
          ? ` (incl. +${rewardOverride.streak_bonus_xp} Streak)`
          : "";
      masteryLastGainBadge.textContent = `+${state.mastery.last_xp_earned} XP${bonusSuffix}`;
      masteryLastGainBadge.classList.remove("hidden");
    } else {
      masteryLastGainBadge.classList.add("hidden");
    }
  }

  const catalogBadges = state.gamificationCatalog.badges || [];
  const unlockedSet = new Set(state.mastery.unlocked_badges || []);
  const newlyUnlockedSet = new Set(state.mastery.newly_unlocked_ids || []);

  if (masteryBadgeCount) {
    masteryBadgeCount.textContent = String(unlockedSet.size);
  }

  if (masteryBadgesGrid) {
    masteryBadgesGrid.innerHTML = "";
    catalogBadges.forEach((badge) => {
      const isUnlocked = unlockedSet.has(badge.id);
      const isJustUnlocked = newlyUnlockedSet.has(badge.id);
      const chip = document.createElement("div");
      chip.className = `mastery-badge-chip ${
        isJustUnlocked
          ? "badge-just-unlocked"
          : isUnlocked
          ? "badge-unlocked"
          : "badge-locked"
      }`;
      chip.title = badge.description || badge.name;
      chip.innerHTML = `
        <span>${escapeHtml(badge.icon)}</span>
        <span>${escapeHtml(badge.name)}</span>
      `;
      masteryBadgesGrid.appendChild(chip);
    });
  }
}

/**
 * Populate Column 3: Enablement Coach & HITL Verification Checkboxes
 */
function renderEnablementCoach(coaching) {
  coachEmptyState.classList.add("hidden");
  coachContentContainer.classList.remove("hidden");

  const score = Number(coaching.prompt_quality_score ?? 0);
  promptScoreBadge.textContent = `Prompt Score: ${score}/100`;
  coachScoreValue.textContent = `${score} / 100`;
  coachScoreBar.style.width = `${Math.min(100, Math.max(0, score))}%`;

  coachWhyText.textContent = coaching.why_your_prompt_caused_this || "";
  coachUpgradedPrompt.textContent = coaching.upgraded_prompt_template || "";

  // Build interactive Human-in-the-Loop (HITL) checkboxes
  const checklistItems = coaching.human_verification_checklist || [];
  state.hitlCheckedMap = {};
  hitlChecklistContainer.innerHTML = "";

  checklistItems.forEach((itemText, idx) => {
    state.hitlCheckedMap[idx] = false;

    const labelEl = document.createElement("label");
    labelEl.className = "hitl-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.hitlIndex = String(idx);

    checkbox.addEventListener("change", (e) => {
      const checked = e.target.checked;
      state.hitlCheckedMap[idx] = checked;
      labelEl.classList.toggle("checked", checked);
      enforceStrictExportGate();
    });

    const span = document.createElement("span");
    span.textContent = itemText;

    labelEl.appendChild(checkbox);
    labelEl.appendChild(span);
    hitlChecklistContainer.appendChild(labelEl);
  });
}

/**
 * CORE INVARIANT #1: Strict Gatekeeping
 * The "Unlock & Export Final Document" button MUST remain disabled (`disabled = true`) until:
 *   1. gate_status === "PASSED" on all Required and Prohibited criteria (final_status === "PASSED")
 *   2. Every checkbox in enablement_coaching.human_verification_checklist is manually checked.
 */
function enforceStrictExportGate() {
  const pipeline = state.latestPipelineResponse;
  const attempts = pipeline?.attempts || [];
  const latestAttempt = attempts[attempts.length - 1];

  // Deterministically verify both final_status and individual checks on the final attempt
  const allReqPassed =
    latestAttempt &&
    Array.isArray(latestAttempt.evaluation?.required_checks) &&
    latestAttempt.evaluation.required_checks.length > 0 &&
    latestAttempt.evaluation.required_checks.every((c) => c.passed === true);

  const noProhViolated =
    latestAttempt &&
    Array.isArray(latestAttempt.evaluation?.prohibited_checks) &&
    latestAttempt.evaluation.prohibited_checks.every((c) => c.violated === false);

  const noUnanchoredCitations =
    latestAttempt &&
    Array.isArray(latestAttempt.evaluation?.citation_audit || []) &&
    (latestAttempt.evaluation.citation_audit || []).every(
      (c) => c.status !== "UNANCHORED" && c.courtlistener_status !== "NOT_FOUND_404"
    );

  const isGatePassed =
    pipeline?.final_status === "PASSED" &&
    latestAttempt?.evaluation?.gate_status === "PASSED" &&
    allReqPassed &&
    noProhViolated &&
    noUnanchoredCitations;

  // Verify HITL checkboxes
  const totalCheckboxes = Object.keys(state.hitlCheckedMap).length;
  const checkedCount = Object.values(state.hitlCheckedMap).filter(Boolean).length;
  const allHitlChecked = totalCheckboxes > 0 && checkedCount === totalCheckboxes;

  // Update HITL Badge
  if (totalCheckboxes > 0) {
    hitlProgressBadge.textContent = `${checkedCount} / ${totalCheckboxes} Verified`;
    hitlProgressBadge.className = `tag-pill ${allHitlChecked ? "tag-unlocked" : "tag-locked"}`;
  } else {
    hitlProgressBadge.textContent = "0 / 0 Verified";
    hitlProgressBadge.className = "tag-pill tag-locked";
  }

  // Update visual requirement indicators
  gateReqCompliance.classList.toggle("satisfied", Boolean(isGatePassed));
  gateReqComplianceIcon.innerHTML = isGatePassed ? "&#9989;" : "&#128274;";

  gateReqHitl.classList.toggle("satisfied", Boolean(allHitlChecked));
  gateReqHitlIcon.innerHTML = allHitlChecked ? "&#9989;" : "&#128274;";

  // Strictly control export button disabled property
  const canUnlockExport = Boolean(isGatePassed && allHitlChecked);
  exportDocBtn.disabled = !canUnlockExport;
  exportDocBtn.classList.toggle("unlocked", canUnlockExport);
  exportDocBtn.innerHTML = canUnlockExport
    ? "&#128275; Unlock &amp; Export Final Document"
    : "&#128274; Unlock &amp; Export Final Document";
}

function buildExportCertificateText() {
  const pipeline = state.latestPipelineResponse;
  if (!pipeline || !pipeline.attempts?.length) return "";

  const finalAttempt = pipeline.attempts[pipeline.attempts.length - 1];
  const checklist = finalAttempt.evaluation.enablement_coaching.human_verification_checklist || [];
  const timestamp = new Date().toISOString();

  return [
    "================================================================================",
    "GATEKEEPER & COACH — VERIFIED COMPLIANCE EXPORT CERTIFICATE",
    "================================================================================",
    `Timestamp (UTC)      : ${timestamp}`,
    `Regulated Domain ID  : ${pipeline.domain_id}`,
    `Final Gate Status    : ${pipeline.final_status} (Attempt #${finalAttempt.attempt_number} of ${pipeline.attempts.length})`,
    `Required Checks      : ${finalAttempt.evaluation.required_checks.length}/${finalAttempt.evaluation.required_checks.length} PASSED`,
    `Prohibited Checks    : 0 VIOLATIONS`,
    "",
    "HUMAN-IN-THE-LOOP (HITL) ATTESTATION (MANUALLY VERIFIED BY PRACTITIONER):",
    ...checklist.map((item, i) => `  [X] ${i + 1}. ${item}`),
    "================================================================================",
    "",
    "FINAL APPROVED DOCUMENT:",
    "--------------------------------------------------------------------------------",
    finalAttempt.draft_text,
    "--------------------------------------------------------------------------------",
  ].join("\n");
}

function showErrorBanner(msg) {
  auditErrorBanner.textContent = msg;
  auditErrorBanner.classList.remove("hidden");
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Event Listeners
domainSelect.addEventListener("change", (e) => {
  setActiveDomain(e.target.value);
  resetPipelineResults();
});

loadFinraScenarioBtn.addEventListener("click", () => {
  if (state.scenarios.finra_scenario) {
    applyScenario(state.scenarios.finra_scenario);
  }
});

loadLegalScenarioBtn.addEventListener("click", () => {
  if (state.scenarios.legal_scenario) {
    applyScenario(state.scenarios.legal_scenario);
  }
});

runPipelineBtn.addEventListener("click", runGatekeeperLoop);

applyUpgradedPromptBtn.addEventListener("click", () => {
  const upgraded = coachUpgradedPrompt.textContent.trim();
  if (upgraded) {
    practitionerPromptInput.value = upgraded;
    practitionerPromptInput.focus();
  }
});

if (resetMasteryBtn) {
  resetMasteryBtn.addEventListener("click", () => {
    state.mastery = {
      total_xp: 0,
      first_pass_streak: 0,
      unlocked_badges: [],
      last_xp_earned: 0,
      newly_unlocked_ids: [],
    };
    saveMasteryState();
    renderMasteryCard();
  });
}

exportDocBtn.addEventListener("click", () => {
  if (exportDocBtn.disabled) return;
  exportDocumentPreview.textContent = buildExportCertificateText();
  exportModal.classList.remove("hidden");
});

closeExportModalBtn.addEventListener("click", () => {
  exportModal.classList.add("hidden");
});

exportModal.addEventListener("click", (e) => {
  if (e.target === exportModal) {
    exportModal.classList.add("hidden");
  }
});

copyExportBtn.addEventListener("click", async () => {
  const text = exportDocumentPreview.textContent;
  await navigator.clipboard.writeText(text);
  const original = copyExportBtn.textContent;
  copyExportBtn.textContent = "Copied!";
  setTimeout(() => {
    copyExportBtn.textContent = original;
  }, 1500);
});

downloadExportBtn.addEventListener("click", () => {
  const text = exportDocumentPreview.textContent;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `verified_${state.activeDomainId || "document"}_export.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

// Boot Workbench
document.addEventListener("DOMContentLoaded", initWorkbench);
