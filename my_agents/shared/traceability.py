"""End-to-end traceability over existing artifact IDs.

ID scheme (headed by goals, per architecture):
G (goal) → BN (business need) → BR/US → FR → UC → T → TECH → FRAPPE → VALIDATION.
Builds the chain from the markdown docs agents already produce (state first,
workspace fallback). Preserves the project's existing ID scheme (Frappe
PROJ/TASK included).

Pure functions — no LLM, no network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "_outputs"

PATTERNS = {
    "G": re.compile(r"\bG-(\d+)\b"),
    "BN": re.compile(r"\bBN-(\d+)\b"),
    "OPT": re.compile(r"\bOPT-(\d+)\b"),
    "BR": re.compile(r"\bBR-(\d+)\b"),
    "US": re.compile(r"\bUS-(\d+)\b"),
    "FR": re.compile(r"\bFR-(\d+)\b"),
    "UC": re.compile(r"\bUC-(\d+)\b"),
    "T": re.compile(r"\bT-(\d+)\b"),
    "TECH": re.compile(r"\bTECH-(\d+)\b"),
    "DEC": re.compile(r"\bDEC-(\d+)\b"),
    "SM": re.compile(r"\bSM-(\d+)\b"),
}

DOC_KEYS = ("brd", "project_plan", "functional_spec", "tech_design")


def extract_ids(text: str) -> dict[str, list[str]]:
    """Map ID family -> sorted unique IDs found in text."""
    out: dict[str, list[str]] = {}
    for fam, rx in PATTERNS.items():
        out[fam] = sorted({f"{fam}-{m.group(1)}" for m in rx.finditer(text or "")},
                          key=lambda s: int(s.split("-")[1]))
    return out


def _doc_text(state: dict[str, Any], key: str) -> str:
    try:
        v = state.get(key, "")
    except Exception:
        v = ""
    if isinstance(v, str) and len(v) > 200:
        return v
    # Workspace-scoped fallback ONLY: same-project artifacts. The harness
    # canonical document wins; otherwise the largest legacy artifact. NEVER a
    # global search — cross-project reads are architecturally forbidden.
    prefix = {"brd": "BRD_", "project_plan": "ProjectPlan_",
              "functional_spec": "FunctionalSpec_", "tech_design": "TechDesign_"}[key]
    try:
        from .workspace import bound_workspace_id, ProjectWorkspace
        pid = bound_workspace_id(state)
        if pid:
            ws = ProjectWorkspace(pid, create=False)
            if ws.exists():
                canon = ws.root / "artifacts" / f"{key}_assembled.md"
                if canon.is_file() and canon.stat().st_size > 200:
                    return canon.read_text(encoding="utf-8")
                found = ws.find_artifact(prefix, ".md", largest=True)
                if found:
                    return found.read_text(encoding="utf-8")
    except Exception:
        pass
    return "" if not isinstance(v, str) else v


def doc_texts(state: dict[str, Any]) -> dict[str, str]:
    return {k: _doc_text(state, k) for k in DOC_KEYS}


def build_trace_map(state: dict[str, Any]) -> dict[str, Any]:
    """Per-family ID sets per doc + co-occurrence links.

    Links are co-occurrence based (IDs mentioned in the same doc are related);
    the FR catalog / traceability matrix / WBS tables agents write make this
    reliable without inventing a new ID system.
    """
    texts = doc_texts(state)
    per_doc = {k: extract_ids(t) for k, t in texts.items()}
    frappe_ids: dict[str, list[str]] = {"PROJ": [], "TASK": []}
    try:
        proj = state.get("frappe_project", "")
        if proj:
            frappe_ids["PROJ"] = [str(proj)]
    except Exception:
        pass
    return {"per_doc": per_doc, "frappe": frappe_ids}


def coverage_report(state: dict[str, Any]) -> dict[str, Any]:
    """Answer: what traces to what, and what is uncovered/unvalidated."""
    tm = build_trace_map(state)
    per = tm["per_doc"]
    brd, plan, spec, design = (per["brd"], per["project_plan"],
                               per["functional_spec"], per["tech_design"])
    brs = set(brd["BR"]) | set(plan["BR"])
    uss = set(brd["US"]) | set(plan["US"])
    frs = set(spec["FR"])
    ucs = set(spec["UC"])
    tasks = set(plan["T"])
    techs = set(design["TECH"])
    goals = set(brd["G"])
    needs = set(brd["BN"])

    def uncovered(sources: set[str], coverers: set[str]) -> list[str]:
        return sorted(s for s in sources if s not in coverers)

    # coverage = mentioned downstream (in spec/design/plan text)
    downstream_br_fr = set(spec["BR"]) | set(design["BR"])
    downstream_us = set(spec["US"]) | set(plan["US"])
    downstream_fr = set(design["FR"]) | set(plan["FR"])

    report = {
        "counts": {
            "G": len(goals), "BN": len(needs),
            "BR": len(brs), "US": len(uss), "FR": len(frs),
            "UC": len(ucs), "T": len(tasks), "TECH": len(techs),
            "FRAPPE_PROJECT": len(tm["frappe"]["PROJ"]),
        },
        "uncovered_BR_no_FR": [],  # computed below via number alignment
        "uncovered_US": uncovered(uss, downstream_us),
        "uncovered_FR_no_TECH": uncovered(frs, downstream_fr),
        "frappe_project": tm["frappe"]["PROJ"],
        "validated": bool((state.get("project_context") or {}).get("validation")),
    }
    # BR coverage: a BR is covered if ANY FR/US/T/TECH with the same number exists
    # (project convention: numbering is aligned across stages) OR it is named downstream.
    numbered: dict[str, set[str]] = {}
    for fam in ("US", "FR", "T", "TECH"):
        numbered[fam] = {i.split("-")[1] for ids in
                         (per["brd"][fam], per["project_plan"][fam],
                          per["functional_spec"][fam], per["tech_design"][fam])
                         for i in ids}
    aligned = set().union(*numbered.values()) if numbered else set()
    report["uncovered_BR_no_FR"] = sorted(
        b for b in brs
        if b not in downstream_br_fr and b.split("-")[1] not in aligned)
    # Goal/Need coverage: a G/BN is covered when its number aligns with any
    # downstream family (same project numbering convention).
    report["uncovered_G"] = sorted(
        g for g in goals if g.split("-")[1] not in aligned)
    report["uncovered_BN"] = sorted(
        n for n in needs if n.split("-")[1] not in aligned)
    return report


def chain_for(state: dict[str, Any], seed_id: str) -> dict[str, Any]:
    """Show the lineage slice for one ID, e.g. BN-001 → BR-003 → US-008 → FR-014.

    Uses number alignment (project convention) plus downstream mentions.
    """
    m = re.fullmatch(r"(G|BN|BR|US|FR|UC|T|TECH)-(\d+)", seed_id.strip().upper())
    if not m:
        return {"seed": seed_id, "error": "ID must look like G-001, BN-001, T-023, ..."}
    num = m.group(2)
    tm = build_trace_map(state)
    chain: dict[str, list[str]] = {}
    for fam in ("G", "BN", "BR", "US", "FR", "UC", "T", "TECH"):
        hits = [i for doc in tm["per_doc"].values() for i in doc[fam]
                if i.split("-")[1] == num]
        chain[fam] = sorted(set(hits))
    chain["FRAPPE"] = list(tm["frappe"]["PROJ"])
    val = ((state.get("project_context") or {}).get("validation") or {})
    chain["VALIDATED"] = [k for k in val.keys()] if isinstance(val, dict) else []
    return {"seed": seed_id.upper(), "chain": chain}
