"""Root-cause analysis — scored classification into an improvement target (prod).

Targets: prompt, knowledge, reasoning_rule, workflow, question_strategy,
validation_rule, artifact_template, classification_rule, model_config,
evaluation_coverage.

Design (deterministic, no LLM):
- Every target owns weighted phrases (multi-word phrases weigh more than
  single words). Each phrase counts at most once per feedback (no stuffing).
- Verdict priors add weight (e.g. misclassified -> classification_rule).
- ID-shape hints add weight (FR-xxx + NFR cue, OQ-xxx/ASSUMPTION cue, ...).
- Scores are summed; the top target wins with confidence = top / total.
  Ties break by fixed TARGETS order (deterministic). runner_up is returned
  for observability; low-confidence (<0.35) results still return the top
  target but callers may route them to human triage.

The agent proposes; a human approves. Never auto-rewrites production prompts.
"""

from __future__ import annotations

import re

TARGETS = (
    "prompt",
    "knowledge",
    "reasoning_rule",
    "workflow",
    "question_strategy",
    "validation_rule",
    "artifact_template",
    "classification_rule",
    "model_config",
    "evaluation_coverage",
)

# target -> ((phrase, weight), ...). Phrases matched case-insensitively;
# multi-word cues carry higher weight than generic single words.
_SIGNALS: dict[str, tuple[tuple[str, float], ...]] = {
    "classification_rule": (
        ("misclassified", 3.0),
        ("classification", 2.5),
        ("filed as nfr", 3.0),
        ("filed as a non-functional", 3.0),
        ("wrong type", 2.0),
        ("should be fr", 2.5),
        ("should be an fr", 2.5),
        ("functional requirement", 1.5),
        ("non-functional", 1.5),
        ("nfr", 1.5),
        ("mislabeled", 2.0),
    ),
    "question_strategy": (
        ("never asked", 3.0),
        ("not asked", 2.5),
        ("missing requirement", 2.0),
        ("missing rule", 2.0),
        ("never captured", 2.0),
        ("elicit", 2.0),
        ("5 whys", 2.0),
        ("five whys", 2.0),
        ("happy path", 2.0),
        ("question", 1.0),
        ("stakeholder never", 2.0),
        ("assumption instead of asking", 2.5),
    ),
    "validation_rule": (
        ("acceptance criteria", 2.5),
        ("acceptance", 1.5),
        ("criteria vague", 2.5),
        ("vague criteria", 2.5),
        ("ambiguous", 2.0),
        ("not testable", 2.5),
        ("untestable", 2.5),
        ("contradict", 2.0),
        ("inconsistent", 1.5),
        ("validation", 1.5),
        ("given/when/then", 2.0),
    ),
    "artifact_template": (
        ("template", 2.5),
        ("section missing", 2.5),
        ("missing section", 2.5),
        ("scope out", 2.0),
        ("glossary", 1.5),
        ("traceability matrix", 2.5),
        ("raci", 1.5),
        ("kpi", 1.5),
    ),
    "workflow": (
        ("wrong stage", 2.5),
        ("stage order", 2.5),
        ("back-edge", 2.0),
        ("back edge", 2.0),
        ("iteration", 1.5),
        ("rework loop", 2.5),
        ("skipped stage", 2.5),
        ("s6", 1.0),
    ),
    "prompt": (
        ("instruction", 2.0),
        ("wording", 2.0),
        ("anchor", 2.0),
        ("tone", 1.5),
        ("system prompt", 2.5),
    ),
    "knowledge": (
        ("domain term", 2.5),
        ("terminology", 2.0),
        ("glossary term", 2.0),
        ("business term renamed", 2.5),
        ("term inconsistent", 1.5),
    ),
    "model_config": (
        ("temperature", 2.5),
        ("token limit", 2.0),
        ("truncated", 1.5),
        ("model config", 2.5),
        ("max tokens", 2.0),
    ),
    "reasoning_rule": (
        ("hallucinat", 3.0),
        ("invented", 2.5),
        ("made up", 2.5),
        ("no evidence", 2.0),
        ("wrong priority", 2.0),
        ("moscow", 2.0),
        ("prioritization", 1.5),
        ("reasoning", 1.5),
    ),
    "evaluation_coverage": (
        ("regression", 2.0),
        ("golden", 2.0),
        ("coverage", 1.5),
        ("not caught by", 2.5),
        ("slipped through", 2.5),
        ("eval", 1.0),
    ),
}

# verdict -> {target: prior weight}
_PRIORS: dict[str, dict[str, float]] = {
    "misclassified": {"classification_rule": 3.0},
    "missing_requirement": {"question_strategy": 2.0},
    "incomplete": {"question_strategy": 1.5, "artifact_template": 1.0},
    "ambiguous": {"validation_rule": 2.0},
    "inconsistent": {"validation_rule": 1.5, "knowledge": 1.0},
    "hallucinated": {"reasoning_rule": 3.0},
    "wrong": {"reasoning_rule": 1.5},
    "bad_question": {"question_strategy": 3.0},
    "bad_prioritization": {"reasoning_rule": 2.5},
    "poor_methodology": {"workflow": 1.5, "prompt": 1.0},
    "improvement_suggested": {},
}

_LOW_CONFIDENCE = 0.35


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def score(feedback: dict) -> dict[str, float]:
    """Raw per-target scores (deterministic)."""
    text = _norm(f"{feedback.get('verdict', '')} {feedback.get('detail', '')}")
    scores: dict[str, float] = {t: 0.0 for t in TARGETS}
    for target, phrases in _SIGNALS.items():
        for phrase, weight in phrases:
            if phrase in text:
                scores[target] += weight
    for target, w in _PRIORS.get(str(feedback.get("verdict", "")), {}).items():
        scores[target] = scores.get(target, 0.0) + w
    # ID-shape hints.
    ids = " ".join(str(feedback.get("related_ids", "")))
    detail = text
    if re.search(r"\bFR-\d+", feedback.get("detail", ""), re.IGNORECASE) and (
        "nfr" in detail or "non-functional" in detail or "classif" in detail
    ):
        scores["classification_rule"] += 1.5
    if ("oq-" in detail or "assumption" in detail) and (
        "ask" in detail or "missing" in detail or "never" in detail
    ):
        scores["question_strategy"] += 1.5
    return scores


def analyze(feedback: dict) -> dict:
    """Return {target, confidence, runner_up, rationale, scores}."""
    scores = score(feedback)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], TARGETS.index(kv[0])))
    top, top_score = ranked[0]
    runner, runner_score = ranked[1]
    total = sum(scores.values())
    if top_score <= 0:
        v = str(feedback.get("verdict", ""))
        if v in ("incomplete", "missing_requirement"):
            top, total, top_score = "question_strategy", 1.0, 1.0
        elif v in ("inconsistent", "ambiguous"):
            top, total, top_score = "validation_rule", 1.0, 1.0
        else:
            top, total, top_score = "prompt", 1.0, 1.0
        return {
            "target": top,
            "confidence": 0.0,
            "runner_up": runner,
            "rationale": f"no signal matched in {feedback.get('id', '?')}; fallback to {top}",
            "scores": scores,
        }
    confidence = round(top_score / total, 3) if total > 0 else 0.0
    rationale = (
        f"top signal for {top} (score {top_score:.1f} vs {runner} {runner_score:.1f}) "
        f"in {feedback.get('id', '?')}"
        + ("; LOW CONFIDENCE — suggest human triage" if confidence < _LOW_CONFIDENCE else "")
    )
    return {
        "target": top,
        "confidence": confidence,
        "runner_up": runner,
        "rationale": rationale,
        "scores": scores,
    }
