"""Shared production tools for BA / Project / Functional / Technical agents.

Design goals:
- Deterministic file outputs (markdown + mermaid + JSON + figma-like HTML viewer)
- Handoff via session state so pipeline stages can consume prior outputs
- Isolation: ALL files go to the CURRENT project workspace
  (projects/<project_id>/artifacts/) — never a shared global folder.
  Standalone runs get an isolated per-session workspace.
- No network calls, no secrets, safe to run in adk web dev server
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from .harness import has_section as _has_section
from .harness import replace_section as _replace_section
from .harness import title_match as _title_match  # noqa: F401  (re-exported for tests)

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "_outputs"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

_MERMAID_HEADS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "gantt",
    "erDiagram",
    "classDiagram",
    "stateDiagram",
    "pie",
    "mindmap",
    "timeline",
)

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  *{{box-sizing:border-box}} body{{margin:0;font-family:Inter,system-ui,Arial,sans-serif;background:#0f1115;color:#e8eaf0}}
  header{{display:flex;gap:12px;align-items:center;padding:12px 16px;background:#171a21;border-bottom:1px solid #2a2f3a;position:sticky;top:0;z-index:5}}
  header h1{{font-size:15px;margin:0;font-weight:600}} header span{{font-size:12px;color:#9aa3b2}}
  .toolbar{{margin-left:auto;display:flex;gap:8px}} button{{background:#232936;color:#fff;border:1px solid #343c4c;border-radius:8px;padding:6px 10px;cursor:pointer}}
  button:hover{{background:#2e3646}} #canvas{{height:calc(100vh - 57px);overflow:auto;cursor:grab;background-image:radial-gradient(#2a3040 1px,transparent 1px);background-size:22px 22px}}
  #inner{{transform-origin:0 0;padding:40px;min-width:max-content}} .mermaid{{background:#fff;border-radius:12px;padding:24px;box-shadow:0 8px 30px rgba(0,0,0,.4)}}
  footer{{position:fixed;bottom:10px;left:12px;font-size:11px;color:#8b93a5}} a{{color:#8ab4ff}}
</style></head><body>
<header><h1>{title}</h1><span>{kind} &middot; figma-like canvas &middot; drag to pan</span>
<div class="toolbar"><button onclick="zoom(0.15)">Zoom +</button><button onclick="zoom(-0.15)">Zoom −</button><button onclick="reset()">Reset</button><button onclick="download()">Download .mmd</button></div></header>
<div id="canvas"><div id="inner"><pre class="mermaid">{mermaid_esc}</pre></div></div>
<footer>Generated {ts} &middot; open <code>{mmd_file}</code> in any Mermaid viewer &middot; <code>{json_file}</code> holds nodes/edges for Figma import</footer>
<script>
let s=1; const inner=document.getElementById('inner');
function zoom(d){{s=Math.min(3,Math.max(0.3,s+d));inner.style.transform=`scale(${{s}})`;}}
function reset(){{s=1;inner.style.transform='scale(1)';document.getElementById('canvas').scrollTo(0,0);}}
function download(){{const b=new Blob([document.getElementById('src').textContent],{{type:'text/plain'}});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='{mmd_file}';a.click();}}
mermaid.initialize({{startOnLoad:true,theme:'neutral',securityLevel:'loose'}});
const cv=document.getElementById('canvas');let dn=false,sx=0,sy=0,sl=0,st=0;
cv.addEventListener('mousedown',e=>{{dn=true;sx=e.clientX;sy=e.clientY;sl=cv.scrollLeft;st=cv.scrollTop;cv.style.cursor='grabbing';}});
addEventListener('mouseup',()=>{{dn=false;cv.style.cursor='grab';}});
cv.addEventListener('mousemove',e=>{{if(!dn)return;cv.scrollLeft=sl-(e.clientX-sx);cv.scrollTop=st-(e.clientY-sy);}});
</script>
<script id="src" type="text/plain">{mermaid_esc}</script>
</body></html>
"""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_")
    return s[:80] or "diagram"


def _extract_graph(mermaid: str) -> dict:
    """Best-effort nodes/edges extraction for Figma/ReactFlow import."""
    nodes: dict[str, str] = {}
    edges: list[dict] = []
    for line in mermaid.splitlines():
        line = line.strip().strip(";")
        if not line or line.startswith("%%"):
            continue
        # A --> B / A-->|label|B / A -->|label| B / A==>B
        m = re.search(
            r"([A-Za-z0-9_]+)\s*[-=.]+>+\s*(\|([^|]*)\|)?\s*([A-Za-z0-9_]+)",
            line,
        )
        if m:
            a, label, b = m.group(1), (m.group(3) or "").strip(), m.group(4)
            nodes.setdefault(a, a)
            nodes.setdefault(b, b)
            edges.append({"from": a, "to": b, "label": label})
            continue
        # A[Label] / A(Label) / A{{Label}} / A["Label"]
        for mm in re.finditer(
            r"([A-Za-z0-9_]+)[\[\({]+([^\]\)\}]+)[\]\)\}]+", line
        ):
            nid, label = mm.group(1), mm.group(2).strip().strip('"').strip("'")
            if nid not in ("graph", "flowchart", "subgraph", "end"):
                nodes[nid] = label or nid
    return {
        "nodes": [{"id": k, "label": v} for k, v in nodes.items()],
        "edges": edges,
    }


def save_doc(
    doc_name: str,
    content_markdown: str,
    tool_context: ToolContext,
) -> dict:
    """Save a markdown deliverable to the CURRENT project workspace + session state.

    Args:
        doc_name: file stem, e.g. 'BRD_shop_floor' (no extension).
        content_markdown: full markdown document text.

    Returns:
        Dict with path, chars, project_id, and state_key used for downstream handoff.
    """
    from .workspace import current_workspace
    stem = _slug(doc_name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"{stem}_{ts}.md"
    ws = current_workspace(tool_context=tool_context)
    path = ws.save_artifact(fname, content_markdown)
    state_key = stem
    try:
        tool_context.state[state_key] = content_markdown
        tool_context.state[f"{stem}_path"] = str(path)
    except Exception:
        pass
    return {
        "path": str(path),
        "file": fname,
        "chars": len(content_markdown),
        "state_key": state_key,
        "project_id": ws.project_id,
    }


def upload_document(
    doc_name: str,
    content_markdown: str,
    tool_context: ToolContext,
) -> dict:
    """Upload a stakeholder source document to the CURRENT project workspace.

    Source material (interview notes, policy PDFs pasted as text, legacy specs)
    the BA cites as evidence. Stored under artifacts/ as uploaded_<stem>_<ts>.md
    — same isolation as deliverables, never shared across projects. Empty
    content and path traversal are refused.

    Args:
        doc_name: human filename, e.g. 'interview_notes_cfo' (no extension).
        content_markdown: pasted document text.

    Returns:
        Dict with path, file, chars, project_id. Unlike save_doc, nothing is
        placed in session state (source docs are pulled, never pushed).
    """
    from pathlib import Path as _Path

    from .workspace import current_workspace
    if not (content_markdown or "").strip():
        return {"ok": False, "error": "empty content refused"}
    stem = _slug(_Path(doc_name).name)
    if not stem:
        return {"ok": False, "error": "unusable document name"}
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"uploaded_{stem}_{ts}.md"
    ws = current_workspace(tool_context=tool_context)
    path = ws.save_artifact(fname, content_markdown)
    return {
        "ok": True,
        "path": str(path),
        "file": fname,
        "chars": len(content_markdown),
        "project_id": ws.project_id,
    }


def append_doc(
    doc_name: str,
    section_title: str,
    content_markdown: str,
    tool_context: ToolContext,
) -> dict:
    """Append ONE section to an assembled document (sectional writing).

    Each call appends `## <section_title>` + content to a single assembly
    file `<stem>_assembled.md` in the CURRENT project workspace and refreshes
    the state key with the full assembled text, so gates read the whole doc
    while the model only ever emits one short section per call.

    Args:
        doc_name: file stem, e.g. 'BRD_travel_expense' (same every call).
        section_title: e.g. 'Scope IN / Scope OUT'.
        content_markdown: ONLY this section's markdown (no re-pasting others).

    Returns:
        Dict with path, total chars, section count, state_key.
    """
    from .workspace import current_workspace
    stem = _slug(doc_name)
    if stem.lower().endswith("_assembled"):
        stem = stem[: -len("_assembled")]
    # Harness-owned placement: during orchestrated runs the orchestrator sets
    # `active_doc_key` so every section lands in ONE canonical file per stage,
    # regardless of the doc_name the model picks.
    try:
        active = tool_context.state.get("active_doc_key")
    except Exception:
        active = None
    if isinstance(active, str) and active.strip():
        stem = _slug(active)
        if stem.lower().endswith("_assembled"):
            stem = stem[: -len("_assembled")]
    fname = f"{stem}_assembled.md"
    ws = current_workspace(tool_context=tool_context)
    try:
        prev = ws.read_artifact(fname)
    except Exception:
        prev = ""
    title = section_title.strip()
    block = f"## {title}\n\n{content_markdown.strip()}\n"
    if prev and _has_section(prev, title):
        if len(content_markdown.strip()) < 50:
            # Guard: never wipe a good section with an empty/narrating call.
            # Keep existing, report state unchanged.
            try:
                tool_context.state[stem] = prev
            except Exception:
                pass
            return {"path": "", "file": fname, "chars": len(prev),
                    "sections": len(re.findall(r"^## \S", prev, re.MULTILINE)),
                    "replaced": False, "kept_existing": True,
                    "state_key": stem, "project_id": ws.project_id}
        full = _replace_section(prev, title, block)
        replaced = True
    else:
        full = (prev + ("\n" if prev and not prev.endswith("\n") else "") + block) if prev else (f"# {stem}\n\n" + block)
        replaced = False
    path = ws.save_artifact(fname, full)
    sections = len(re.findall(r"^## \S", full, re.MULTILINE))
    try:
        tool_context.state[stem] = full
        tool_context.state[f"{stem}_path"] = str(path)
    except Exception:
        pass
    return {
        "path": str(path),
        "file": fname,
        "chars": len(full),
        "sections": sections,
        "replaced": replaced,
        "state_key": stem,
        "project_id": ws.project_id,
    }


def build_diagram_bundle(
    diagram_name: str,
    diagram_kind: str,
    mermaid: str,
    title: str,
    tool_context: ToolContext,
) -> dict:
    """Build a figma-like diagram bundle (.mmd + .json + .html viewer).

    Args:
        diagram_name: file stem, e.g. 'ba_flow_order_to_cash'.
        diagram_kind: one of flow, gantt, sequence, er, architecture, functional.
        mermaid: complete mermaid source starting with flowchart/graph/sequenceDiagram/gantt/erDiagram.
        title: human title shown in the HTML canvas header.

    Returns:
        Dict with mmd/json/html paths, node/edge counts, state_key.
    """
    head = mermaid.strip().splitlines()[0].strip() if mermaid.strip() else ""
    if not head.split()[0] in _MERMAID_HEADS:
        return {
            "error": f"mermaid must start with one of {_MERMAID_HEADS}, got: {head[:80]}",
        }
    stem = _slug(diagram_name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mmd_file = f"{stem}_{ts}.mmd"
    json_file = f"{stem}_{ts}.graph.json"
    html_file = f"{stem}_{ts}.html"
    from .workspace import current_workspace
    ws = current_workspace(tool_context=tool_context)
    ws.save_artifact(mmd_file, mermaid.strip() + "\n")
    graph = _extract_graph(mermaid)
    graph.update({"title": title, "kind": diagram_kind, "mermaid_file": mmd_file})
    ws.save_artifact(json_file, json.dumps(graph, indent=2))
    esc = (
        mermaid.strip()
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html = _HTML_TEMPLATE.format(
        title=title,
        kind=diagram_kind,
        mermaid_esc=esc,
        ts=ts,
        mmd_file=mmd_file,
        json_file=json_file,
    )
    (ws.save_artifact(html_file, html))
    state_key = f"{stem}_diagram"
    mmd_p, json_p, html_p = (ws.root / "artifacts" / mmd_file,
                             ws.root / "artifacts" / json_file,
                             ws.root / "artifacts" / html_file)
    try:
        tool_context.state[state_key] = {
            "title": title,
            "kind": diagram_kind,
            "mermaid": mermaid.strip(),
            "mmd": str(mmd_p),
            "json": str(json_p),
            "html": str(html_p),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        }
    except Exception:
        pass
    return {
        "mmd": str(mmd_p),
        "json": str(json_p),
        "html": str(html_p),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "state_key": state_key,
        "project_id": ws.project_id,
    }


def list_outputs(tool_context: ToolContext = None) -> dict:
    """List deliverables in the CURRENT project workspace (never global).

    Returns:
        Dict with files list (newest last).
    """
    from .workspace import current_workspace
    ws = current_workspace(tool_context=tool_context)
    files = ws.list_artifacts()
    return {"dir": str(ws.root / "artifacts"), "project_id": ws.project_id,
            "count": len(files), "files": files[-50:]}
