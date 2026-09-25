# BA Agent — Operations Runbook

How to run, monitor, recover, and evolve the BA Agent in production.
Code: `my_agents/ba_agent/`. Pipeline: `my_agents/delivery_pipeline/`.

## 1. Normal operation — two phases, one agent

The BA works in whichever register the owner needs:

1. **Conversation (default for a new project).** The owner talks about their
   company, team, plans and pain in plain words. The agent listens, replies like
   a person, asks only what changes the outcome, and captures what it hears with
   `record_knowledge` (ledger `K-nnn`, mirrored into the project sections). **No
   documents are produced and no gate runs** in this phase — the pipeline's
   section machinery and quality checks stay out of the chat. An unanswered
   question is tracked (`ask_user` → an open question), never lost or asked
   twice by accident.
2. **Generation (explicit).** When the owner asks for the artefacts ("write my
   BRD", "generate the documents", or agreeing to the agent's
   `propose_generation`), the formal phases run against everything the
   conversation produced: sections → quality engine → the 28-check gate → BA
   Package → human final review. If knowledge is still thin the agent asks a
   short plain question first instead of dumping gate findings.

After the artefacts are approved the agent **offers** to continue with the next
stage rather than assuming it; the owner decides how far the work goes
(`continue` proceeds, anything else keeps them in review). The pipeline's own
projects (no conversation) still auto-advance.

## 1a. The BA v2 contract (fail-closed)

The gate re-validates every artefact from the BRD text, so a contract added
after a section was written cannot be skipped. Beyond the classic checks the
gate now blocks on:

| Check | Requires |
|---|---|
| `business_case` | Stage 2 artefact: capability gap, ≥2 `OPT-n` options with pro/con and feasibility, exactly one recommended, quantified benefits, explicit risk of doing nothing, decision factors |
| `solution_assessment` | Stage 7 artefact: per-requirement coverage (US/FR → solution element → OPT-n), solution gaps, business acceptance |
| `readiness` | People, process, technology and training each with a status and an action |
| `quality_attributes` | The 8 §15/§16 attributes: no ambiguous/unquantified statements, atomic rules, testable acceptance criteria, no placeholders |
| `priorities_tagged` | Every `US-`/`BR-` carries a MoSCoW tag or `P0–P3` |
| `evidence_coverage` | Every `US-`/`BR-` has an evidence link (§34) or an explicit assumption/OQ tag |
| `assumptions_registered` | Every `[ASSUMPTION:xxx]` has a status; `confirmed` needs evidence + a named decider; `rejected` must leave the BRD |
| `decisions_recorded` | Every approved `AP-xxx` has a linked `DEC-n` decision (recorded automatically on approval) |
| `ba_plan` | Approach, stakeholder strategy and review plan exist; no monitoring variance |
| `outcome_metrics` | ≥1 `SM-n` metric with baseline, target, measurement method and review point |
| `cr_impact_recorded` | No open change request without an impact analysis |
| `contradictions_resolved` | No open blocking stakeholder contradiction |

Tools for these: `record_knowledge`, `knowledge_status`, `ask_user`,
`propose_generation`, `record_decision`, `link_evidence`, `evidence_report`,
`record_assumption`, `set_assumption_status`, `record_ba_plan`,
`update_ba_plan_item`, `ba_plan_status`, `quality_report`,
`resolve_contradiction`, `analyze_change_impact`, `impact_report`,
`record_success_metric`, `record_outcome_measurement`, `outcome_report`.

The package has **34 items**; each renders recorded state where available and
otherwise the accepted BRD content — a gap is reported, never invented.

## 1b. Chat register (verbosity)

`BA_CHAT_VERBOSITY=quiet|normal|technical` (default `quiet`):

- `quiet` — the owner sees plain sentences only: no ids, no stage/section/gate
  vocabulary, no per-section progress. Gate outcomes are phrased as "there are
  still two things I need to fix (…)".
- `technical` — the pipeline's native messages (stage names, check counts,
  section ids, package gap lists). Use for development and QA.
- Discovery turns are agent-authored in every mode; the filter only guards the
  pipeline's own messages.

## 1c. Operator notes (internal mechanics)

What the owner never sees, but the operator may need:

- Every generation pass gets one computed **STAGE TASK S1..S7** directive plus
  pending human items (`ba_agent/stage_engine.py:brief_extras`, hooked in
  `shared/orch_nodes.py`), so no manual staging is needed. Entering any stage
  with gaps auto-builds one templated question batch (`QB-xxx`, ≤5, once per
  stage per change request).
- Gate outcomes, section progress and ids are internal: they reach the owner
  only through the plain-language register above (or verbatim with
  `BA_CHAT_VERBOSITY=technical`).
- A BA gate pass persists `BA_PACKAGE_assembled.md` and records the
  `ba_package` artifact snapshot, then pauses with **WAITING_FOR_HUMAN** for the
  final review (`approve` / revision guidance / `abort`). Conversational
  projects then get the offer-to-continue pause instead of auto-advancing.
- Triage with one call: `ba_ops_snapshot` (tool or
  `ba_agent.ops.ops_snapshot(state)`) — stage, version, eval/HITL queues,
  package readiness, the gate, plan variance, quality findings, evidence
  coverage, outcome status and a `suggested_action`.
- Traceability runs G-001 → BN-001 → OPT-001 → US/BR → FR → UC → T → TECH
  (`chain_for`, `coverage_report`); `reuse_report_tool` reports carried/added/
  dropped ids between versions.
- Conversation state lives in `conversation` (mode, turn, transcript) and the
  captured knowledge in `knowledge` (K-nnn ledger) — both persisted with the
  project, so a resumed session keeps the relationship and the facts.

## 2. Human collaboration ops

| Situation | Action |
|---|---|
| Owner just wants to talk | Nothing to do: discovery needs no setup. They can also start a pipeline project directly with an explicit "generate my BRD" first message |
| Owner asks for the documents | Automatic: the trigger switches to generation in the same turn (after capturing anything they said). Watch `generation_requested` in history |
| Owner asks for the documents too early | The agent asks a short plain question (`generation_blocked_thin`); `pending_generation` makes their next message proceed |
| Owner declines the offer to continue | Stays in review (`continue_declined`); they can still give revision guidance or say `continue` later |
| Stakeholder source material arrives | `upload_document(name, text)` → same-project `uploaded_*` artifact; cite it as evidence, never other projects' files |
| Stakeholder answers arrive | `log_stakeholder_answer(qid, answer)` per answer; batch completes automatically |
| Same question asked twice | Check `ba_question_batches` + elicitation log; `build_batch` dedupes both — file feedback if it recurs |
| Business case needs the decision recorded | The BRD section carries the options; log the outcome with `record_decision(decision, reason, decision_maker, related_ids)` (DEC-xxx) |
| Approval requested | `request_ba_approval(subject, detail)` → human replies → `record_approval_decision(id, approve\|reject, approver)` (an approval auto-records its linked DEC-n) |
| Requirement needs a source | `link_evidence(artifact_id, source_type, source_ref, note)` — cite `EL-xxx` answers, `DEC-n` decisions or `uploaded_*` documents |
| Assumption without OQ | `hitl_status` → `assumption_gaps.unmirrored`; register with `record_assumption`, then add the OQ entry, never drop the tag |
| Confirming an assumption | `set_assumption_status(label, confirmed, evidence_ref, decided_by)` — evidence and a named decider are mandatory |
| Contradiction between answers | brief lists `CT-xxx`; resolve with `resolve_contradiction(id, resolution, resolved_by)` |
| Plan item drifts | `ba_plan_status` → variance; close or waive with `update_ba_plan_item(id, done\|waived)` |
| Change request raised | `analyze_change_impact(cr_id, changed_ids, reason)` — the gate blocks while an open CR has no impact report |
| Post-go-live outcome | `record_outcome_measurement(metric_id, actual)` → `outcome_report(file_feedback=True)` files the gap as feedback |
| Pipeline pauses WAITING_FOR_HUMAN | Reply with guidance/decision, or `abort`; resume grants iterations, sections resume from snapshots |
| Requirements without evidence | `evidence_report` → uncovered ids; link each or tag the assumption/OQ |

## 3. Eval loop ops (controlled evolution — never blind self-edit)

```
Tester verdict (capture_ba_feedback: wrong/incomplete/.../improvement_suggested)
  → root cause (analyze_ba_feedback → target + confidence)
  → candidate (propose_ba_candidate → status proposed, stored in ba_candidates)
  → regression (run_ba_regression: gate + minima + anchors, recorded on the stored copy)
  → human approval (approve_ba_candidate — regression-pass only, named approver)
  → deploy (deploy_ba_version — stored human-approved only; forged copies ignored; idempotent)
```

Rules: deploy takes a **version id**, never a dict; unapproved candidates are
undeployable by construction; deployed candidates are immutable; every step
audits `ba_eval` history. Each deploy also freezes a requirement baseline
snapshot and promotes the validated lesson to improvement memory
(`improvement_memory_report`, grouped by target). Org-wide promotion of
lessons beyond the project boundary stays a manual curation step —
project isolation is never bypassed automatically. Current version: `version_manager.current(state)`;
ledger: `version_manager.history(state)`.

## 4. Accuracy ops (heuristics stay honest)

- Golden sets (run in CI): 12-case AC counter (`test_ba_stages.py`, bar 100%),
  30-case root-cause (`test_ba_eval.py`, bar ≥90%).
- Field labels: `sampling_review_queue` lists unlabeled feedback,
  low-confidence first → tester labels via `record_field_label` →
  `field_accuracy_report` gives rolling accuracy + queue depth.
- Cadence: sample quarterly (or after any field miss); `runner_up` +
  `confidence` drive what to review first. If field accuracy drops below the
  golden bar, treat as an eval-loop feedback item (target: `evaluation_coverage`).

## 5. Recovery

- **Resume**: all BA keys persist to the workspace
  (`ba_eval`, `ba_versions`, `ba_current_version`, `ba_candidates`,
  `ba_stage_*`, `ba_question_batches`, `ba_approvals`, `ba_field_labels`).
  Rebind the project id and `load_project_state`; verify with `ba_ops_snapshot`.
- **Gate stuck failing**: read `suggested_action`; targeted repair rewrites only
  implicated sections (snapshots restore the rest — never hand-edit canonical files).
- **Budget exhausted**: pipeline pauses; reply with a business decision or `abort`.
- **Bad deploy**: ledger is append-only — deploy the previous `human-approved`
  version forward (no destructive rollback).

## 6. Tests

```bash
.venv/bin/pytest my_agents/tests/ -v
```

Suite covers: stage engine/tracker, HITL, package + handoff intake, eval hard
gate, golden accuracy sets, persistence round-trips, and the full delivery
pipeline (stubs, no LLM/network). Keep it green — it is the deploy gate.
