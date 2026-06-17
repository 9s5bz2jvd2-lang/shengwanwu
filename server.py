"""FastAPI bridge for Shengwanwu Loop V0.4.

This server keeps the local-file / JSONL design intact: the API is only a thin
HTTP wrapper around the existing five-gate engine in ``shengwanwu_loop.v04``.
It does not introduce a database, auth, Docker, or deployment machinery.

Run locally:
    uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shengwanwu_loop.cli import append_jsonl, read_jsonl, stable_hash, utc_now, write_jsonl
from shengwanwu_loop.v04 import (
    ensure_kb_v04,
    ingest_sources,
    run_hypothesis,
    run_map,
    run_return,
    run_validate,
)


app = FastAPI(
    title="Shengwanwu Loop API",
    description="Local FastAPI wrapper for the Shengwanwu five-gate hypothesis loop.",
    version="0.4.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="Research goal / focus for this loop run.")
    notes: str = Field(..., min_length=1, description="Distilled Markdown notes to ingest.")
    kb_path: str = Field("knowledge_base", description="Persistent JSONL knowledge base directory.")
    max_gaps: int = Field(12, ge=1, le=200, description="Maximum gaps to map for this run.")
    max_per_gap: int = Field(2, ge=1, le=10, description="Maximum hypotheses generated per gap.")


class ErrorBody(BaseModel):
    error: str


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_kb_path(raw: str) -> Path:
    """Resolve a local KB path without allowing empty or home-expansion surprises."""
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail={"error": "kb_path must not be empty"})
    path = Path(raw).expanduser()
    return path


def _stats(kb: Path) -> Dict[str, Any]:
    ensure_kb_v04(kb)
    nodes = read_jsonl(kb / "nodes.jsonl")
    hyps = read_jsonl(kb / "hypotheses.jsonl")
    gaps = read_jsonl(kb / "gaps.jsonl")
    sources = read_jsonl(kb / "sources.jsonl")
    lineage = read_jsonl(kb / "lineage.jsonl")
    mother_patches = read_jsonl(kb / "mother_patch.jsonl")
    review_states = read_jsonl(kb / "review_state.jsonl")
    return {
        "kb_path": str(kb),
        "total_nodes": len(nodes),
        "total_hypotheses": len(hyps),
        "total_gaps": len(gaps),
        "total_lineage": len(lineage),
        "total_mother_patches": len(mother_patches),
        "total_review_states": len(review_states),
        "sources": [s.get("path", "") for s in sources if s.get("path")],
    }


def _latest_jsonl(path: Path, limit: int) -> List[dict]:
    rows = read_jsonl(path)
    if limit <= 0:
        return []
    return rows[-limit:][::-1]


def _api_notes_to_markdown(notes: str) -> str:
    """Normalize free-form textarea input into Markdown the V0.4 parser can ingest.

    The V0.4 Markdown fallback intentionally extracts bullets, numbered items,
    and substantial long lines. A UI textarea often contains short paragraphs, so
    this wrapper turns ordinary paragraphs into bullets while preserving headings
    and existing lists.
    """
    lines: List[str] = []
    for raw in notes.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
        elif line.startswith("#") or line.startswith(("-", "*", "+")):
            lines.append(raw)
        elif __import__("re").match(r"^\d+[.)]\s+", line):
            lines.append(raw)
        else:
            lines.append(f"- {line}")
    return "\n".join(lines).strip() + "\n"


def _fallback_gap(kb: Path, run_dir: Path, goal: str) -> List[dict]:
    """Create a conservative open-question gap when heuristics find none.

    This keeps the UI useful for small notes while preserving the evidence
    boundary: the gap says only that the current field is too sparse to support
    a mechanism yet.
    """
    nodes = read_jsonl(kb / "nodes.jsonl")
    if not nodes:
        return []
    node = nodes[-1]
    seed = f"open_question|{goal}|{node.get('node_id')}"
    gap = {
        "gap_id": f"gap_{stable_hash(seed)[:12]}",
        "gap_type": "weak_evidence",
        "operator": "FallbackOpenQuestionOperator",
        "description": f"Current notes are too sparse to map a specific mechanism for: {goal}",
        "related_nodes": [node.get("node_id")],
        "related_hypotheses": [],
        "derivation": "No rule-based gap operator fired; the API created a conservative open-question gap so the loop can continue.",
        "evidence_boundary": "Fallback gap: useful as a next-question seed, not as a claim of evidence.",
        "priority_score": 0.35,
        "focus": goal,
        "created_at": utc_now(),
    }
    write_jsonl(run_dir / "gaps.jsonl", [gap])
    known = {g.get("gap_id") for g in read_jsonl(kb / "gaps.jsonl")}
    if gap["gap_id"] not in known:
        append_jsonl(kb / "gaps.jsonl", [gap])
    return [gap]


def _http_400(exc: Exception) -> HTTPException:
    detail = {"error": str(exc) or exc.__class__.__name__}
    return HTTPException(status_code=400, detail=detail)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "engine": "shengwanwu-loop-v04"}


@app.post("/api/run", responses={400: {"model": ErrorBody}})
def run_loop(req: RunRequest) -> Dict[str, Any]:
    """Run one local five-gate loop over Markdown notes.

    Flow:
    notes -> temporary Markdown source -> ingest -> map -> hypothesis -> validate
    -> return/consolidate. The persistent field is the JSONL directory given by
    ``kb_path``.
    """
    try:
        kb = _safe_kb_path(req.kb_path)
        ensure_kb_v04(kb)

        run_id = f"api_{_utc_stamp()}"
        run_dir = Path("runs") / run_id
        input_dir = Path("runs") / "api_inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_path = input_dir / f"{run_id}.md"
        source_path.write_text(_api_notes_to_markdown(req.notes), encoding="utf-8")

        _new_sources, nodes_added = ingest_sources(kb, [str(source_path)], mode="markdown")
        if nodes_added <= 0:
            raise ValueError("notes did not yield any knowledge nodes; add at least one bullet, numbered item, or substantial sentence")
        gaps = run_map(kb, run_dir, req.goal, max_gaps=req.max_gaps)
        if not gaps:
            gaps = _fallback_gap(kb, run_dir, req.goal)
        hypotheses = run_hypothesis(kb, run_dir, req.goal, max_per_gap=req.max_per_gap)
        validations = run_validate(kb, run_dir)
        run_return(kb, run_dir, write_report=True, consolidate=True)

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "source_path": str(source_path),
            "nodes_added": nodes_added,
            "gaps": gaps,
            "hypotheses": hypotheses,
            "validations": validations,
            "lineage": read_jsonl(run_dir / "lineage.jsonl"),
            "mother_patches": read_jsonl(run_dir / "mother_patch.jsonl"),
            "review_states": read_jsonl(run_dir / "review_state.jsonl"),
            "kb_stats": _stats(kb),
        }
    except HTTPException:
        raise
    except SystemExit as exc:
        raise _http_400(exc)
    except Exception as exc:  # keep the public response compact, but log traceback locally
        print(traceback.format_exc())
        raise _http_400(exc)


@app.get("/api/kb/stats", responses={400: {"model": ErrorBody}})
def kb_stats(kb_path: str = Query("knowledge_base")) -> Dict[str, Any]:
    try:
        return _stats(_safe_kb_path(kb_path))
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)


@app.get("/api/kb/hypotheses", responses={400: {"model": ErrorBody}})
def kb_hypotheses(
    kb_path: str = Query("knowledge_base"),
    limit: int = Query(20, ge=1, le=500),
) -> List[dict]:
    try:
        kb = _safe_kb_path(kb_path)
        ensure_kb_v04(kb)
        return _latest_jsonl(kb / "hypotheses.jsonl", limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)


@app.get("/api/kb/lineage", responses={400: {"model": ErrorBody}})
def kb_lineage(
    kb_path: str = Query("knowledge_base"),
    limit: int = Query(20, ge=1, le=500),
) -> List[dict]:
    try:
        kb = _safe_kb_path(kb_path)
        ensure_kb_v04(kb)
        return _latest_jsonl(kb / "lineage.jsonl", limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)


@app.get("/api/kb/mother-patches", responses={400: {"model": ErrorBody}})
def kb_mother_patches(
    kb_path: str = Query("knowledge_base"),
    limit: int = Query(20, ge=1, le=500),
) -> List[dict]:
    try:
        kb = _safe_kb_path(kb_path)
        ensure_kb_v04(kb)
        return _latest_jsonl(kb / "mother_patch.jsonl", limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)


@app.get("/api/kb/review-states", responses={400: {"model": ErrorBody}})
def kb_review_states(
    kb_path: str = Query("knowledge_base"),
    limit: int = Query(20, ge=1, le=500),
) -> List[dict]:
    try:
        kb = _safe_kb_path(kb_path)
        ensure_kb_v04(kb)
        return _latest_jsonl(kb / "review_state.jsonl", limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)


@app.delete("/api/kb/reset", responses={400: {"model": ErrorBody}})
def kb_reset(
    kb_path: str = Query("knowledge_base"),
    confirm: bool = Query(False, description="Must be true to clear the KB."),
) -> Dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail={"error": "confirm=true is required to reset the knowledge base"})
    try:
        kb = _safe_kb_path(kb_path)
        ensure_kb_v04(kb)
        cleared: List[str] = []
        for name in [
            "sources.jsonl",
            "nodes.jsonl",
            "gaps.jsonl",
            "hypotheses.jsonl",
            "validations.jsonl",
            "lineage.jsonl",
            "mother_patch.jsonl",
            "review_state.jsonl",
            "anti_patterns.jsonl",
        ]:
            path = kb / name
            path.write_text("", encoding="utf-8")
            cleared.append(str(path))
        (kb / "world_model.md").write_text("", encoding="utf-8")
        cleared.append(str(kb / "world_model.md"))
        return {"status": "reset", "kb_path": str(kb), "cleared": cleared}
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_400(exc)
