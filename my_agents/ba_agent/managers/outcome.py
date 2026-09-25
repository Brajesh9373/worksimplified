"""Business outcome evaluation (§27) — expected vs actual, with an outcome gap.

The BA defines success metrics (SM-n) with a baseline, a target, a measurement
method and a review point. After the solution is in use, actual measurements are
recorded; the report computes the gap per metric and the gaps are filed back
into the existing improvement-feedback loop — so an unmet business outcome
becomes a first-class BA finding rather than a silent miss.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

METRICS_KEY = "success_metrics"
MEASUREMENTS_KEY = "outcome_measurements"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    """First number in a value ('-40%' -> -40.0, '2 seconds' -> 2.0)."""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import get_context

    return get_context(state)


def record_metric(state: dict[str, Any], name: str, baseline: str = "",
                  target: str = "", method: str = "", review_point: str = "",
                  unit: str = "", metric_id: str = "") -> dict:
    """Register one success metric (SM-n)."""
    try:
        from shared.project_context import commit

        if not (name or "").strip():
            return {"ok": False, "error": "metric name is required"}
        if not (target or "").strip():
            return {"ok": False, "error": "target is required (measurable expected outcome)"}
        if not (method or "").strip():
            return {"ok": False, "error": "measurement method is required"}
        if not (review_point or "").strip():
            return {"ok": False, "error": "review point is required (when it is measured)"}
        ctx = _ctx(state)
        metrics = ctx.get(METRICS_KEY)
        if not isinstance(metrics, list):
            metrics = []
            ctx[METRICS_KEY] = metrics
        mid = (metric_id or f"SM-{len(metrics) + 1:03d}").strip().upper()
        if not re.fullmatch(r"SM-\d+", mid):
            return {"ok": False, "error": "metric_id must look like SM-001"}
        rec = {"id": mid, "name": name.strip()[:200], "baseline": str(baseline).strip()[:80],
               "target": str(target).strip()[:80], "unit": (unit or "").strip()[:40],
               "method": method.strip()[:300], "review_point": review_point.strip()[:120],
               "at": _now()}
        for m in metrics:
            if m.get("id") == mid:
                m.update(rec)
                commit(state)
                return {"ok": True, "metric": dict(m), "duplicate": True}
        metrics.append(rec)
        del metrics[:-50]
        commit(state)
        return {"ok": True, "metric": dict(rec)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def record_measurement(state: dict[str, Any], metric_id: str, actual: str,
                       note: str = "", at: str = "") -> dict:
    """Record an observed value for one metric."""
    try:
        from shared.project_context import commit

        mid = (metric_id or "").strip().upper()
        known = {m.get("id") for m in (_ctx(state).get(METRICS_KEY) or [])
                 if isinstance(m, dict)}
        if mid not in known:
            return {"ok": False, "error": f"unknown metric {metric_id!r}"}
        if not str(actual or "").strip():
            return {"ok": False, "error": "actual value is required"}
        ctx = _ctx(state)
        meas = ctx.get(MEASUREMENTS_KEY)
        if not isinstance(meas, list):
            meas = []
            ctx[MEASUREMENTS_KEY] = meas
        rec = {"metric_id": mid, "actual": str(actual).strip()[:80],
               "note": (note or "").strip()[:300], "at": at or _now()}
        meas.append(rec)
        del meas[:-200]
        commit(state)
        return {"ok": True, "measurement": rec}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def report(state: dict[str, Any]) -> dict:
    """Per-metric expected vs actual, with the outcome gap (§27)."""
    try:
        ctx = _ctx(state)
        metrics = [m for m in (ctx.get(METRICS_KEY) or []) if isinstance(m, dict)]
        meas = [m for m in (ctx.get(MEASUREMENTS_KEY) or []) if isinstance(m, dict)]
        rows: list[dict] = []
        gaps: list[dict] = []
        for m in metrics:
            latest = next((x for x in reversed(meas) if x.get("metric_id") == m.get("id")), None)
            base, target = _num(m.get("baseline")), _num(m.get("target"))
            actual = _num(latest.get("actual")) if latest else None
            status = "pending"
            gap = None
            if actual is not None and target is not None:
                # direction: a target above the baseline means "at least";
                # a target below the baseline means "at most".
                at_least = base is None or target >= base
                met = actual >= target if at_least else actual <= target
                status = "met" if met else "missed"
                if not met:
                    gap = {"metric_id": m.get("id"), "name": m.get("name"),
                           "baseline": m.get("baseline"), "target": m.get("target"),
                           "actual": latest.get("actual") if latest else None,
                           "gap": round(abs(target - actual), 4),
                           "direction": "below target" if at_least else "above target"}
                    gaps.append(gap)
            rows.append({**m, "actual": latest.get("actual") if latest else None,
                         "measured_at": latest.get("at") if latest else None,
                         "status": status})
        return {"ok": True, "metrics": rows, "gaps": gaps,
                "measured": sum(1 for r in rows if r["actual"] is not None),
                "pending": sum(1 for r in rows if r["status"] == "pending")}
    except Exception as e:
        return {"ok": False, "metrics": [], "gaps": [], "measured": 0, "pending": 0,
                "error": str(e)[:200]}


def file_gap_feedback(state: dict[str, Any]) -> dict:
    """§27 loop closure: file each missed outcome as BA improvement feedback."""
    try:
        from ba_agent.eval.feedback_store import capture
        from ba_agent.eval.version_manager import current

        rep = report(state)
        filed: list[str] = []
        for g in rep.get("gaps", []):
            detail = (f"Business outcome gap on {g['metric_id']} ({g['name']}): "
                      f"target {g['target']} vs actual {g['actual']} ({g['direction']})")
            try:
                r = capture(state, "incomplete", detail, current(state), str(g["metric_id"]))
                if r.get("ok") and r.get("feedback"):
                    filed.append(r["feedback"].get("id", ""))
                elif r.get("id"):
                    filed.append(str(r["id"]))
            except Exception:
                continue
        return {"ok": True, "filed": [f for f in filed if f], "gaps": len(rep.get("gaps", []))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "filed": [], "gaps": 0}


def render(state: dict[str, Any]) -> str:
    """Package rendering: metric table plus any outcome gap."""
    try:
        rep = report(state)
        rows = rep.get("metrics") or []
        if not rows:
            return ""
        lines = [f"| {m['id']} | {m['name']} | {m.get('baseline') or '-'} | "
                 f"{m.get('target') or '-'} | {m.get('actual') or '-'} | {m['status']} |"
                 for m in rows]
        body = ("| Metric | Name | Baseline | Target | Actual | Status |\n"
                "|---|---|---|---|---|---|\n" + "\n".join(lines))
        for m in rows:
            body += (f"\n- {m['id']} measured by: {m.get('method')} at {m.get('review_point')}")
        if rep.get("gaps"):
            body += "\n\n**Outcome gaps**\n" + "\n".join(
                f"- {g['metric_id']} {g['name']}: {g['direction']} (gap {g['gap']})"
                for g in rep["gaps"])
        return body
    except Exception:
        return ""
