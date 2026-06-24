#!/usr/bin/env python3
"""Stdlib-only smoke test for the V0.5 T1 relation-explicitization layer.

Run:  python3 -m tests.test_relations_t1    (from repo root)
  or: python3 tests/test_relations_t1.py

Covers the T1 acceptance bullets:
  * `build_relations` / `link` materializes relations.jsonl with the T0 contract
    fields and evidence anchors (never a verified causal fact),
  * a full V0.4-style run stamps `from_relations` on gaps AND hypotheses and the
    relations they point to all exist (no C2 dangling),
  * after a fresh T1 run, validate-graph is 0 errors / 0 warnings with
    relations > 0,
  * an OLD KB whose hypotheses predate the relation layer still passes
    validate-graph (warnings only, never an error / crash) before AND after a
    standalone `link`.

Everything runs in a temp dir; no network, no Venus corpus dependency.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shengwanwu_loop import graph, v04  # noqa: E402

# Fixture rich enough to fire contradiction + missing_link + temporal_drift,
# with shared tags so single-node gaps can find a candidate counterpart.
FIXTURE = [
    {"unit_id": "u1", "node_id": "u1_001", "node_type": "finding",
     "content": "The clouds are mainly concentrated sulfuric acid (H2SO4) with water (H2O) "
                "as a secondary component; acidity is extreme and water activity is low.",
     "tags": ["composition", "H2SO4", "acidity", "water"]},
    {"unit_id": "u1", "node_id": "u1_002", "node_type": "concept",
     "content": "Habitability of the cloud layer has been proposed, suggesting possible "
                "biogenic life and amino acid chemistry despite the acidity.",
     "tags": ["habitability", "life", "amino acid"]},
    {"unit_id": "u1", "node_id": "u1_003", "node_type": "limitation",
     "content": "Stability of amino acid biomolecules in concentrated sulfuric acid is "
                "uncertain; deamidation and sulfonation reactions may degrade them.",
     "tags": ["amino acid", "stability", "H2SO4"]},
    {"unit_id": "u2", "node_id": "u2_001", "node_type": "result",
     "content": "Cloud-top temperature shows temporal variation with latitude and altitude, "
                "modulated by thermal tides and diurnal forcing.",
     "tags": ["cloud-top temperature", "temporal variation", "thermal tide", "altitude"]},
    {"unit_id": "u2", "node_id": "u2_002", "node_type": "finding",
     "content": "A UV absorber of unknown identity affects cloud-top albedo; its link to SO2 "
                "and H2SO4 aerosol chemistry across altitude and latitude is debated.",
     "tags": ["UV_absorber", "SO2", "altitude", "H2SO4"]},
]

REL_CONTRACT_FIELDS = (
    "relation_id", "relation_type", "from_node", "to_node", "direction",
    "polarity", "basis", "evidence_refs", "method", "confidence",
    "counter_evidence", "boundary", "spawned_gap_id", "review_state",
    "created_by", "created_at", "from_gap", "human_review_required",
)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _write_fixture_kb(kb: Path) -> None:
    v04.ensure_kb_v04(kb)
    nodes = [v04._normalize_distilled_row(r, Path("fixture.jsonl"), "src_test", i)
             for i, r in enumerate(FIXTURE, 1)]
    # Preserve the explicit node_ids from the fixture.
    for n, r in zip(nodes, FIXTURE):
        n["node_id"] = r["node_id"]
    v04.write_jsonl(kb / "nodes.jsonl", nodes)


def _checks(report, level):
    return {item["check"] for item in report[level]}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="swwl_t1_smoke_"))
    try:
        focus = "physical chemistry of Venus clouds"

        # --- Case A: build_relations materializes a contract-clean layer ---
        kb = tmp / "kb_link"
        _write_fixture_kb(kb)
        result = graph.build_relations(kb, focus=focus)
        rels = result["relations"]
        _assert(result["count"] > 0, "build_relations produced no relations")
        _assert((kb / graph.RELATIONS_FILE).exists(), "relations.jsonl not written")
        for r in rels:
            for f in REL_CONTRACT_FIELDS:
                _assert(f in r, f"relation missing contract field {f}")
            _assert(r["relation_type"] in graph.RELATION_TYPES, "bad relation_type")
            _assert(r["polarity"] in graph.POLARITIES, "bad polarity")
            _assert(r["method"] in graph.RELATION_METHODS, "bad method")
            _assert(r["confidence"] in graph.RELATION_CONFIDENCE, "bad confidence")
            _assert(r["human_review_required"] is True,
                    "candidate edge must require human review (never a verified fact)")
            _assert(r["confidence"] != "verified",
                    "T1 distilled edge must never be verified")
            # C5: each evidence_ref anchors to a node with a unit_id/quote pointer.
            _assert(r["evidence_refs"], "relation has no evidence_refs")
            for ref in r["evidence_refs"]:
                _assert(ref.get("node_id") and (ref.get("quote") or ref.get("unit_id")),
                        "evidence_ref lacks node/quote/unit pointer")
            _assert((r["basis"] or "").strip(), "relation basis must be non-empty (C6)")
        rel_types = {r["relation_type"] for r in rels}
        _assert("contradict" in rel_types, "expected a contradiction relation")
        print(f"OK A: build_relations -> {result['count']} contract-clean relations "
              f"(types={sorted(rel_types)})")

        # --- Case B: full run stamps from_relations on gaps AND hypotheses ---
        kb2 = tmp / "kb_run"
        out2 = tmp / "run"
        _write_fixture_kb(kb2)
        gaps = v04.run_map(kb2, out2, focus, max_gaps=60)
        rel_ids = {r["relation_id"] for r in v04.read_jsonl(kb2 / graph.RELATIONS_FILE)}
        _assert(rel_ids, "run map produced no relations")
        linked_gaps = [g for g in gaps if g.get("from_relations")]
        _assert(linked_gaps, "no gap received from_relations")
        for g in gaps:
            _assert("from_relations" in g, "gap missing from_relations key")
            for rid in g["from_relations"]:
                _assert(rid in rel_ids, f"gap from_relations {rid} dangling (C2)")

        hyps = v04.run_hypothesis(kb2, out2, focus, max_per_gap=2)
        for h in hyps:
            _assert("from_relations" in h, "hypothesis missing from_relations key")
            for rid in h["from_relations"]:
                _assert(rid in rel_ids, f"hypothesis from_relations {rid} dangling (C2)")
        anchored = [h for h in hyps if h.get("from_relations")]
        _assert(anchored, "no hypothesis received from_relations")
        print(f"OK B: run map+hypothesis -> {len(linked_gaps)}/{len(gaps)} gaps and "
              f"{len(anchored)}/{len(hyps)} hypotheses carry from_relations")

        # --- Case C: fresh T1 run validate-graph is 0 errors / 0 warnings ---
        rep = graph.validate_graph(kb2)
        _assert(rep["summary"]["relations"] > 0, "validate-graph saw 0 relations")
        _assert(rep["summary"]["ok"], f"fresh T1 run must have 0 errors; {rep['errors']}")
        _assert(not rep["warnings"],
                f"fresh T1 run must have 0 warnings; got {_checks(rep, 'warnings')}")
        print(f"OK C: fresh T1 run validate-graph -> 0 errors / 0 warnings "
              f"(relations={rep['summary']['relations']})")

        # --- Case D: old KB whose hypotheses predate relations -> warn, no crash ---
        kb3 = tmp / "kb_old"
        _write_fixture_kb(kb3)
        # Simulate an old run: hypotheses WITHOUT from_relations, no relations.jsonl.
        old_hyps = [{"hypothesis_id": "hyp_old000000", "gap_id": "gap_x",
                     "claim": "legacy", "source_nodes": ["u1_001"]}]
        v04.write_jsonl(kb3 / "hypotheses.jsonl", old_hyps)
        rep_pre = graph.validate_graph(kb3)
        _assert(rep_pre["summary"]["ok"], "old KB (no relations) must not error")
        _assert("C2" in _checks(rep_pre, "warnings"),
                "old KB without relations.jsonl should warn C2")
        # Standalone link builds relations but must not crash on legacy hyps.
        link_res = graph.build_relations(kb3, focus=focus)
        _assert(link_res["count"] > 0, "link produced no relations on old KB")
        rep_post = graph.validate_graph(kb3)
        _assert(rep_post["summary"]["ok"],
                f"old KB after link must still pass (no errors); {rep_post['errors']}")
        _assert("C7" in _checks(rep_post, "warnings"),
                "legacy hypothesis without from_relations should warn C7, not error")
        print("OK D: old KB hypotheses without from_relations -> warnings only, no crash")

        print("\nALL OK: relation-layer T1 smoke test passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
