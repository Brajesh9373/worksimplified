"""Coverage → live diagram data. Pure functions, no I/O, no LLM.

The console renders one node per catalogue group (A–G); a node lights up as
its requirements are fulfilled. ``build_flow`` is called on every turn, so the
diagram always agrees with the checklist.
"""

from __future__ import annotations

from .catalogue import GROUPS, ITEMS, ORDER, item_applies

RESOLVED = ("fulfilled", "assumed")


def applicable_items(project_type: str | None) -> list[str]:
    """Item ids the interview must resolve, in interview order."""
    if project_type is None:
        return [i for i in ORDER if ITEMS[i]["group"] == "A"]
    return [i for i in ORDER if item_applies(ITEMS[i], project_type)]


def coverage(session: dict) -> dict:
    """{resolved, total, pct} over the applicable items."""
    ids = applicable_items(session.get("project_type"))
    items = session.get("items", {})
    resolved = sum(1 for i in ids if items.get(i, {}).get("status") in RESOLVED)
    total = len(ids)
    return {"resolved": resolved, "total": total,
            "pct": round(100 * resolved / total) if total else 100}


def build_flow(session: dict) -> dict:
    """Diagram data: nodes with per-group progress, sequential edges."""
    project_type = session.get("project_type")
    items = session.get("items", {})
    ids = applicable_items(project_type)
    current = next((i for i in ids
                    if items.get(i, {}).get("status") not in RESOLVED), None)
    nodes = []
    for group in GROUPS:
        gid = group["id"]
        gids = [i for i in ids if ITEMS[i]["group"] == gid]
        if not gids:
            nodes.append({"id": gid, "label": group["label"], "desc": group["desc"],
                          "resolved": 0, "total": 0, "status": "na"})
            continue
        resolved = sum(1 for i in gids if items.get(i, {}).get("status") in RESOLVED)
        if resolved == len(gids):
            status = "done"
        elif current is not None and ITEMS[current]["group"] == gid:
            status = "active"
        else:
            status = "pending"
        nodes.append({"id": gid, "label": group["label"], "desc": group["desc"],
                      "resolved": resolved, "total": len(gids), "status": status})
    live = [n["id"] for n in nodes if n["status"] != "na"]
    edges = [[live[k], live[k + 1]] for k in range(len(live) - 1)]
    cov = coverage(session)
    return {"nodes": nodes, "edges": edges, "pct": cov["pct"],
            "resolved": cov["resolved"], "total": cov["total"],
            "current_item": current}
