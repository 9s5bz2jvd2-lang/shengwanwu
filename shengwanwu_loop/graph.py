#!/usr/bin/env python3
"""Shengwanwu Loop — relation-graph contract layer (V0.4 x Arbor, T0).

This module makes the *relation* layer of the knowledge field an explicit,
machine-checkable first-class object and provides a read-only structural
validator.  It implements ONLY the T0 contract from the work method spec
(`work/shengwanwu_v04_arbor_work_method_20260624.md`, §3.3 + §4):

    * a minimal RelationNode schema (`relations.jsonl`)
    * `validate_graph(kb_dir)` — pure structural / consistency checks, C1–C10

It does NOT build relations (T1 `build_relations`), does NOT do backflow
(T2 `return --propagate`) and never judges scientific truth.  The validator
only reports `{"errors": [...], "warnings": [...]}`; an old KB that has no
`relations.jsonl` yet passes with warnings rather than failing (spec §7 T0
acceptance: "关系层暂空时只发 C2/C7 warning，不报 error").

Everything here is Python stdlib only — no LLM, no network.

Red lines honored (spec §8.1):
    * no score -> prune/merge scalar chain; this validator emits no scores.
    * a keyword-only relation is downgraded to a warning, never an error.
    * mother_patch never auto-promotes; C9 flags any non-accepted leakage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .cli import read_jsonl, stable_hash, utc_now

# ---------------------------------------------------------------------------
# RelationNode schema (spec §3.3)
# ---------------------------------------------------------------------------

RELATIONS_FILE = "relations.jsonl"

RELATION_TYPES = {"support", "contradict", "causal", "transfer", "cooccur", "missing"}
POLARITIES = {"support", "weaken", "conflict", "neutral", "unresolved"}
RELATION_METHODS = {"string", "metadata", "manual", "llm_assisted"}
RELATION_CONFIDENCE = {"verified", "inferred", "analogical", "speculative"}

# Append-only state machines (spec §3.6 ReviewNode / §3.7 MotherPatchNode).
REVIEW_TRANSITIONS: Dict[str, List[str]] = {
    "candidate": ["contested", "reviewed", "rejected"],
    "contested": ["reviewed", "rejected"],
    "reviewed": ["supported", "rejected"],
    "supported": ["contested"],
    "rejected": [],
}
PATCH_TRANSITIONS: Dict[str, List[str]] = {
    "proposed": ["reviewed", "rejected", "superseded"],
    "reviewed": ["accepted", "rejected", "superseded"],
    "accepted": ["superseded"],
    "rejected": [],
    "superseded": [],
}


def relation_id(seed: str) -> str:
    """Stable relation id, prefixed `rel_` per the existing id convention."""
    return "rel_" + stable_hash(seed)[:12]


def make_relation(
    from_node: str,
    to_node: str,
    relation_type: str,
    basis: str,
    evidence_refs: Sequence[dict],
    *,
    direction: str = "directed",
    polarity: str = "neutral",
    method: str = "metadata",
    confidence: str = "inferred",
    counter_evidence: Optional[Sequence[dict]] = None,
    boundary: str = "",
    spawned_gap_id: Optional[str] = None,
    created_by: str = "manual",
) -> dict:
    """Build one RelationNode row (the minimal §3.3 contract).

    This is a pure constructor — it writes nothing.  T1 `build_relations`
    will use it; for T0 it only exists so tests and callers can mint
    well-formed rows without duplicating the field set.
    """
    seed = f"{relation_type}|{from_node}|{to_node}|{basis}"
    return {
        "relation_id": relation_id(seed),
        "relation_type": relation_type,
        "from_node": from_node,
        "to_node": to_node,
        "direction": direction,
        "polarity": polarity,
        "basis": basis,
        "evidence_refs": list(evidence_refs),
        "method": method,
        "confidence": confidence,
        "counter_evidence": list(counter_evidence or []),
        "boundary": boundary,
        "spawned_gap_id": spawned_gap_id,
        "review_state": "candidate",
        "created_by": created_by,
        "created_at": utc_now(),
    }


# ---------------------------------------------------------------------------
# build_relations (T1): make the relation layer explicit, deterministically
# ---------------------------------------------------------------------------
#
# Design (spec §3.3 + work-method §3.3): relations are *candidate evidence
# edges* between distilled nodes, never asserted scientific causes.  T1 builds
# them deterministically (stdlib only, no LLM, no network) by reusing the same
# gap operators the field already runs, then turning each gap's `related_nodes`
# into typed, evidence-anchored RelationNodes.
#
# Why derive from gaps rather than raw keyword co-occurrence: the gap operators
# already encode the *reason* two nodes are linked (a contradiction axis, a
# co-mentioned mechanism, a temporal-variability cluster).  Reusing them keeps
# the relation's `basis` a real derivation string, not a bare keyword hit — so
# every relation has a non-empty mechanistic basis and clears the C6 red line
# ("keyword-only relation must stay speculative").  Each relation also carries
# `from_gap`, so `map` can attach the matching `from_relations` to each gap and
# `hypothesis` can transparently propagate them.
#
# Every relation built here is method="metadata", confidence="inferred",
# human_review_required=True — it is a literature-distillation edge awaiting the
# six-eyes / human gate, NOT a verified causal claim.

# gap_type -> (relation_type, polarity).  Only multi-node gap types yield edges;
# single-node gaps (weak_evidence) describe one node and have no endpoint pair.
_GAP_RELATION_MAP: Dict[str, tuple] = {
    "contradiction": ("contradict", "conflict"),
    "missing_link": ("missing", "unresolved"),
    "temporal_drift": ("causal", "unresolved"),
    "physical_chemistry_mechanism": ("causal", "unresolved"),
    "planetary_science_synthesis": ("transfer", "neutral"),
    "cross_domain_transfer": ("transfer", "neutral"),
    # weak_evidence / failed_hypothesis are single-node gaps about one under-
    # evidenced claim.  We still emit a candidate corroboration edge to the most
    # similar field node, so the claim is anchored to *something* checkable
    # rather than floating as an unanchored noise candidate (polarity stays
    # `unresolved`: the edge says "this needs corroboration", not "this holds").
    "weak_evidence": ("cooccur", "unresolved"),
    "failed_hypothesis": ("cooccur", "unresolved"),
}


def _node_evidence_ref(node: dict) -> dict:
    """Build one evidence_ref anchored to a distilled node (C5: has unit_id/quote)."""
    span = node.get("evidence_span") or {}
    quote = span.get("quote") or (node.get("content", "") or "")[:300]
    return {
        "node_id": node.get("node_id"),
        "unit_id": node.get("unit_id") or span.get("unit_id"),
        "source_id": node.get("source_id"),
        "quote": quote[:300],
    }


def _relation_pairs(related: Sequence[str]) -> List[tuple]:
    """Star pairing: link the first related node to each subsequent one.

    A gap's `related_nodes` are co-implicated by one operator; the first node is
    the anchor (e.g. the habitability side of a contradiction axis) and the rest
    are the counterparts.  Star (not clique) keeps edge count linear and the
    semantics readable — one anchor, several candidate counterparts.
    """
    related = [r for r in dict.fromkeys(related) if r]  # stable-unique
    if len(related) < 2:
        return []
    anchor = related[0]
    return [(anchor, other) for other in related[1:]]


def _counterpart_for_single(anchor_node: dict, nodes: List[dict],
                            max_links: int = 2) -> List[str]:
    """Deterministically pick counterpart node(s) for a single-node gap.

    Single-node gaps (missing_link / temporal_drift) flag *one* node whose own
    content co-mentions terms that should be linked.  To make that an explicit
    candidate edge we need a second endpoint: the other field node(s) that share
    the most tags with the anchor.  This is a *candidate* link from co-occurring
    distilled units — never a verified causal edge — so the relation it produces
    keeps confidence="inferred" and human_review_required=True.

    Selection is fully deterministic: rank other nodes by (shared-tag count desc,
    node_id asc); shared-tag count must be >= 1 so we never invent a link out of
    nothing.  Returns up to ``max_links`` node ids (possibly empty).
    """
    anchor_id = anchor_node.get("node_id")
    anchor_tags = {t.lower() for t in (anchor_node.get("tags", []) or [])}
    anchor_text = (anchor_node.get("content", "") or "").lower()

    # Primary signal: shared tags. Fallback signal: shared mechanism/temporal
    # vocabulary in the node content (the same lexicon the gap operators used to
    # flag this node), so a node with hyper-specific tags still gets a candidate
    # counterpart instead of an orphan gap.
    from . import v04
    anchor_terms = {term for term in (v04.MECHANISM_TERMS + v04.TEMPORAL_TERMS)
                    if term in anchor_text}

    scored = []
    for n in nodes:
        nid = n.get("node_id")
        if not nid or nid == anchor_id:
            continue
        shared_tags = len(anchor_tags & {t.lower() for t in (n.get("tags", []) or [])})
        ntext = (n.get("content", "") or "").lower()
        shared_terms = sum(1 for term in anchor_terms if term in ntext)
        score = shared_tags * 10 + shared_terms  # tags dominate, terms break ties
        if score >= 1:
            scored.append((-score, nid))
    scored.sort()
    return [nid for _, nid in scored[:max_links]]


def build_relations(kb_dir, focus: str = "", max_gaps: int = 200,
                    gaps: Optional[List[dict]] = None) -> dict:
    """Deterministically construct the relation layer for a knowledge base.

    Reads ``nodes.jsonl`` from ``kb_dir`` and emits ``relations.jsonl`` — one
    RelationNode per (anchor, counterpart) pair implied by a multi-node gap.
    Pure stdlib; writes only ``relations.jsonl`` inside ``kb_dir``.

    Relations are candidate literature-distillation edges, never verified causal
    facts: every row is method="metadata", confidence="inferred",
    human_review_required=True, with evidence_refs anchored to the endpoint
    nodes' distilled quotes.

    Returns ``{"relations": [...], "gap_relations": {gap_id: [relation_id,...]},
    "count": int, "warnings": [...]}``.  ``gap_relations`` lets the caller stamp
    ``from_relations`` onto gaps deterministically.

    If ``gaps`` is provided, those exact gaps are used (so a run reuses the same
    gap set it mapped); otherwise gaps are rebuilt from nodes via the V0.4
    operators so ``link`` can stand alone against any KB.
    """
    from . import v04  # local import: graph<->v04 would otherwise cycle at import

    kb = Path(kb_dir)
    warnings: List[str] = []
    nodes = read_jsonl(kb / "nodes.jsonl")
    if not nodes:
        warnings.append("no nodes.jsonl (or empty): cannot build relations")
        write_jsonl_relations(kb, [])
        return {"relations": [], "gap_relations": {}, "count": 0, "warnings": warnings}

    nodes_by_id = {n.get("node_id"): n for n in nodes if n.get("node_id")}
    if gaps is None:
        gaps = v04.build_gaps_v04(nodes, focus, max_gaps=max_gaps)

    relations: List[dict] = []
    gap_relations: Dict[str, List[str]] = {}
    seen_rel_ids: set = set()

    for gap in gaps:
        gid = gap.get("gap_id")
        gt = gap.get("gap_type")
        mapping = _GAP_RELATION_MAP.get(gt)
        if mapping is None:
            continue  # single-node / non-edge gap type (e.g. weak_evidence)
        rel_type, polarity = mapping
        basis = (gap.get("derivation") or gap.get("description") or "").strip()
        boundary = gap.get("evidence_boundary", "")
        gap_relations.setdefault(gid, [])

        related = [r for r in dict.fromkeys(gap.get("related_nodes", []) or []) if r]
        if len(related) >= 2:
            pairs = _relation_pairs(related)
        elif len(related) == 1:
            # Single-node gap (missing_link / temporal_drift): synthesize a
            # candidate edge to the most tag-similar field node(s).
            anchor_id = related[0]
            anchor_node = nodes_by_id.get(anchor_id)
            pairs = ([(anchor_id, cp) for cp in
                      _counterpart_for_single(anchor_node, nodes)]
                     if anchor_node else [])
        else:
            pairs = []

        for a, b in pairs:
            na, nb = nodes_by_id.get(a), nodes_by_id.get(b)
            if not (na and nb):
                continue  # never point at a node outside the field (C1)
            evidence_refs = [_node_evidence_ref(na), _node_evidence_ref(nb)]
            rel = make_relation(
                a, b, rel_type,
                basis=basis,
                evidence_refs=evidence_refs,
                polarity=polarity,
                method="metadata",
                confidence="inferred",
                boundary=boundary,
                created_by="build_relations",
            )
            rel["from_gap"] = gid
            rel["focus"] = focus or gap.get("focus", "")
            # A distilled edge always needs a human before it counts as a finding.
            rel["human_review_required"] = True
            rid = rel["relation_id"]
            if rid in seen_rel_ids:
                # Same (type, anchor, counterpart, basis) surfaced by two gaps:
                # keep one row, but record the edge under both gaps.
                if rid not in gap_relations[gid]:
                    gap_relations[gid].append(rid)
                continue
            seen_rel_ids.add(rid)
            relations.append(rel)
            gap_relations[gid].append(rid)

    write_jsonl_relations(kb, relations)
    return {
        "relations": relations,
        "gap_relations": gap_relations,
        "count": len(relations),
        "warnings": warnings,
    }


def write_jsonl_relations(kb: Path, relations: List[dict]) -> None:
    """Write the relation store, sorted-key JSONL, matching the field's style."""
    from .cli import write_jsonl

    write_jsonl(Path(kb) / RELATIONS_FILE, relations)


# ---------------------------------------------------------------------------
# validate_graph (spec §4, checks C1–C10)
# ---------------------------------------------------------------------------


def _err(out: dict, check: str, msg: str) -> None:
    out["errors"].append({"check": check, "message": msg})


def _warn(out: dict, check: str, msg: str) -> None:
    out["warnings"].append({"check": check, "message": msg})


def _dup_ids(rows: List[dict], key: str) -> List[str]:
    seen: set = set()
    dups: List[str] = []
    for r in rows:
        rid = r.get(key)
        if rid is None:
            continue
        if rid in seen and rid not in dups:
            dups.append(rid)
        seen.add(rid)
    return dups


def validate_graph(kb_dir) -> dict:
    """Structural / consistency check of the knowledge field's relation graph.

    Pure read-only.  Returns ``{"errors": [...], "warnings": [...], "summary": {...}}``.
    ``errors`` non-empty means the run must not advance to G5 (spec §4).
    Never judges scientific correctness — only graph completeness.

    An old KB without ``relations.jsonl`` (e.g. the Venus knowledge_base_v04)
    passes with C2/C7 warnings rather than failing (spec §7 T0 acceptance).
    """
    kb = Path(kb_dir)
    out: dict = {"errors": [], "warnings": [], "summary": {}}

    if not kb.exists():
        _err(out, "C0", f"kb_dir does not exist: {kb}")
        return out

    nodes = read_jsonl(kb / "nodes.jsonl")
    relations = read_jsonl(kb / RELATIONS_FILE)
    gaps = read_jsonl(kb / "gaps.jsonl")
    hypotheses = read_jsonl(kb / "hypotheses.jsonl")
    review_states = read_jsonl(kb / "review_state.jsonl")
    mother_patches = read_jsonl(kb / "mother_patch.jsonl")

    node_ids = {n.get("node_id") for n in nodes if n.get("node_id")}
    relation_ids = {r.get("relation_id") for r in relations if r.get("relation_id")}
    gap_ids = {g.get("gap_id") for g in gaps if g.get("gap_id")}

    relations_present = (kb / RELATIONS_FILE).exists() and bool(relations)
    if not relations_present:
        # Old KB / fresh KB: the relation layer is simply not built yet.
        _warn(out, "C2",
              f"no {RELATIONS_FILE} (or empty): relation layer not built yet; "
              "treating as warning per T0 contract, not failing.")

    # --- C1: dangling pointers into nodes.jsonl ---
    for r in relations:
        rid = r.get("relation_id", "<no-id>")
        for fld in ("from_node", "to_node"):
            nid = r.get(fld)
            if nid is not None and nid not in node_ids:
                _err(out, "C1", f"relation {rid}: {fld}={nid!r} not in nodes.jsonl")
        for ref in r.get("evidence_refs", []) or []:
            nid = ref.get("node_id") if isinstance(ref, dict) else None
            if nid is not None and nid not in node_ids:
                _err(out, "C1", f"relation {rid}: evidence_ref node_id={nid!r} not in nodes.jsonl")
    for g in gaps:
        gid = g.get("gap_id", "<no-id>")
        for nid in g.get("related_nodes", []) or []:
            if nid not in node_ids:
                _err(out, "C1", f"gap {gid}: related_node {nid!r} not in nodes.jsonl")
    for h in hypotheses:
        hid = h.get("hypothesis_id", "<no-id>")
        for nid in h.get("source_nodes", []) or []:
            if nid not in node_ids:
                _err(out, "C1", f"hypothesis {hid}: source_node {nid!r} not in nodes.jsonl")

    # --- C2: dangling from_relations on gaps / hypotheses ---
    for g in gaps:
        gid = g.get("gap_id", "<no-id>")
        for rel in g.get("from_relations", []) or []:
            if rel not in relation_ids:
                _err(out, "C2", f"gap {gid}: from_relations {rel!r} not in {RELATIONS_FILE}")
    for h in hypotheses:
        hid = h.get("hypothesis_id", "<no-id>")
        for rel in h.get("from_relations", []) or []:
            if rel not in relation_ids:
                _err(out, "C2", f"hypothesis {hid}: from_relations {rel!r} not in {RELATIONS_FILE}")

    # --- C3: dangling spawned_gap_id on relations ---
    for r in relations:
        rid = r.get("relation_id", "<no-id>")
        sg = r.get("spawned_gap_id")
        if sg is not None and sg not in gap_ids:
            _err(out, "C3", f"relation {rid}: spawned_gap_id={sg!r} not in gaps.jsonl")

    # --- C4: globally unique ids per store ---
    for rows, key, store in (
        (nodes, "node_id", "nodes.jsonl"),
        (relations, "relation_id", RELATIONS_FILE),
        (gaps, "gap_id", "gaps.jsonl"),
        (hypotheses, "hypothesis_id", "hypotheses.jsonl"),
        (review_states, "review_state_id", "review_state.jsonl"),
        (mother_patches, "mother_patch_id", "mother_patch.jsonl"),
    ):
        for dup in _dup_ids(rows, key):
            _err(out, "C4", f"duplicate {key}={dup!r} in {store}")

    # --- C5 / C6 / C10: per-relation evidence + method + polarity ---
    for r in relations:
        rid = r.get("relation_id", "<no-id>")
        refs = r.get("evidence_refs", []) or []
        conf = r.get("confidence")
        method = r.get("method")
        basis = (r.get("basis") or "").strip()

        # C5: every relation needs evidence_refs; verified ones MUST, others warn.
        if not refs:
            if conf == "verified":
                _err(out, "C5", f"relation {rid}: confidence=verified but no evidence_refs")
            else:
                _warn(out, "C5", f"relation {rid}: no evidence_refs (confidence={conf})")
        else:
            for ref in refs:
                if not (isinstance(ref, dict) and (ref.get("quote") or ref.get("unit_id"))):
                    _warn(out, "C5",
                          f"relation {rid}: evidence_ref lacks quote/unit_id pointer")

        # C6: string/keyword-only relation with no mechanistic basis -> speculative.
        if method == "string" and not basis:
            if conf != "speculative":
                _warn(out, "C6",
                      f"relation {rid}: method=string with empty basis (keyword-only) "
                      f"should be confidence=speculative, got {conf!r}")

        # C10: polarity must be an explicit tri-state, not defaulted/boolean.
        pol = r.get("polarity")
        if pol is None:
            _warn(out, "C10", f"relation {rid}: polarity missing (must be explicit tri-state)")
        elif pol not in POLARITIES:
            _warn(out, "C10",
                  f"relation {rid}: polarity={pol!r} not in {sorted(POLARITIES)}")

    # --- C7: hypothesis not anchored to any relation edge -> noise candidate ---
    if relations_present:
        for h in hypotheses:
            hid = h.get("hypothesis_id", "<no-id>")
            if not (h.get("from_relations") or []):
                _warn(out, "C7",
                      f"hypothesis {hid}: empty from_relations -> noise_candidate; "
                      "must not enter supported")

    # --- C8: illegal state transitions (append-only, no jump out of rejected) ---
    _check_transitions(out, review_states, "review_state_id", "from_stage", "to_stage",
                       "stage", REVIEW_TRANSITIONS, "C8", "ReviewNode")
    _check_transitions(out, mother_patches, "mother_patch_id", "from_status", "to_status",
                       "patch_status", PATCH_TRANSITIONS, "C8", "MotherPatchNode")

    # --- C9: world_model conclusions must not leak non-accepted patches ---
    _check_world_model_leakage(out, kb, mother_patches)

    out["summary"] = {
        "nodes": len(nodes),
        "relations": len(relations),
        "gaps": len(gaps),
        "hypotheses": len(hypotheses),
        "review_states": len(review_states),
        "mother_patches": len(mother_patches),
        "errors": len(out["errors"]),
        "warnings": len(out["warnings"]),
        "ok": not out["errors"],
        "relations_present": relations_present,
    }
    return out


def _check_transitions(out: dict, rows: List[dict], id_key: str,
                       from_key: str, to_key: str, stage_key: str,
                       transitions: Dict[str, List[str]], check: str, label: str) -> None:
    """C8: validate any recorded from->to transition against the allowed map.

    Rows that carry no explicit ``from_*`` (e.g. the current snapshot rows the
    existing pipeline writes) are not transitions and are skipped — T0 only
    flags an *illegal* transition when one is actually recorded.
    """
    for row in rows:
        rid = row.get(id_key, "<no-id>")
        frm = row.get(from_key)
        to = row.get(to_key)
        if frm is None and to is None:
            continue  # snapshot row, not an append-only transition record
        # An entry-state row (from=None, to=candidate/proposed) is legal.
        if frm is None:
            if to is not None and to not in transitions:
                _err(out, check, f"{label} {rid}: unknown {to_key}={to!r}")
            continue
        if frm not in transitions:
            _err(out, check, f"{label} {rid}: unknown {from_key}={frm!r}")
            continue
        allowed = transitions[frm]
        if to not in allowed:
            _err(out, check,
                 f"{label} {rid}: illegal transition {frm!r} -> {to!r} "
                 f"(allowed: {allowed or 'terminal'})")
        # Also honor a per-row allowed_transitions map if present.
        row_allowed = row.get("allowed_transitions")
        if isinstance(row_allowed, dict) and frm in row_allowed:
            if to not in (row_allowed.get(frm) or []):
                _err(out, check,
                     f"{label} {rid}: transition {frm!r} -> {to!r} violates "
                     "row-declared allowed_transitions")


def _check_world_model_leakage(out: dict, kb: Path, mother_patches: List[dict]) -> None:
    """C9: a conclusion section in world_model.md must contain nothing that
    traces to a mother_patch whose patch_status != 'accepted'.

    T0 keeps this conservative: it only fires when world_model.md has an
    explicit conclusion/结论 section AND a non-accepted patch's claim text is
    found verbatim inside it.  This avoids false positives on the existing
    Venus world_model (which has no conclusion section), while still catching
    the red-line violation the spec forbids.
    """
    wm = kb / "world_model.md"
    if not wm.exists():
        return
    text = wm.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Collect the body of any conclusion section.
    conclusion_markers = ("结论", "conclusion", "established", "确证")
    in_conclusion = False
    conclusion_body: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            in_conclusion = any(m in heading for m in conclusion_markers)
            continue
        if in_conclusion:
            conclusion_body.append(line)
    if not conclusion_body:
        return
    body = "\n".join(conclusion_body)

    for p in mother_patches:
        if p.get("patch_status") == "accepted":
            continue
        for add in p.get("add", []) or []:
            content = (add.get("content") if isinstance(add, dict) else None) or ""
            content = content.strip()
            if content and content in body:
                _err(out, "C9",
                     f"world_model conclusion section contains content from "
                     f"non-accepted patch {p.get('mother_patch_id')!r} "
                     f"(patch_status={p.get('patch_status')!r})")
