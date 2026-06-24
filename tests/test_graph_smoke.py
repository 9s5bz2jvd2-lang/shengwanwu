#!/usr/bin/env python3
"""Stdlib-only smoke test for the V0.4 x Arbor T0 relation-graph contract.

Run:  python3 -m tests.test_graph_smoke    (from repo root)
  or: python3 tests/test_graph_smoke.py

Covers the T0 acceptance bullets:
  * a valid relation passes validate_graph (0 errors),
  * a dangling relation pointer is reported as an error,
  * an old Venus KB without relations.jsonl warns (C2) but does NOT crash,
  * duplicate ids (C4), illegal state transitions (C8), and world_model
    leakage (C9) are caught as errors.

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

FIXTURE = [
    {"unit_id": "u1", "node_id": "u1_001", "node_type": "finding",
     "content": "The clouds are mainly concentrated sulfuric acid (H2SO4); acidity is extreme.",
     "tags": ["composition", "H2SO4", "acidity"]},
    {"unit_id": "u1", "node_id": "u1_002", "node_type": "concept",
     "content": "Habitability of the cloud layer has been proposed despite the environment.",
     "tags": ["habitability", "life"]},
]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _checks(report, level):
    return {item["check"] for item in report[level]}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="swwl_graph_smoke_"))
    try:
        # --- Case A: valid relation passes (0 errors) ---
        kb = tmp / "kb_valid"
        v04.ensure_kb_v04(kb)
        _write_jsonl(kb / "nodes.jsonl", FIXTURE)
        rel = graph.make_relation(
            "u1_002", "u1_001", "contradict",
            basis="conclusion_conflict",
            evidence_refs=[{"node_id": "u1_002", "unit_id": "u1", "quote": "habitability proposed"},
                           {"node_id": "u1_001", "unit_id": "u1", "quote": "extreme acidity"}],
            polarity="conflict", method="metadata", confidence="inferred",
        )
        _write_jsonl(kb / graph.RELATIONS_FILE, [rel])
        rep = graph.validate_graph(kb)
        _assert(rep["summary"]["ok"], f"valid KB should pass; errors={rep['errors']}")
        _assert(rep["summary"]["relations"] == 1, "expected 1 relation counted")
        print("OK A: valid relation -> 0 errors")

        # --- Case B: dangling relation pointer -> C1 error ---
        kb2 = tmp / "kb_dangling"
        v04.ensure_kb_v04(kb2)
        _write_jsonl(kb2 / "nodes.jsonl", FIXTURE)
        bad = graph.make_relation(
            "u1_001", "ghost_999", "causal", basis="variable_chain",
            evidence_refs=[{"node_id": "u1_001", "unit_id": "u1", "quote": "x"}],
            polarity="support",
        )
        _write_jsonl(kb2 / graph.RELATIONS_FILE, [bad])
        rep2 = graph.validate_graph(kb2)
        _assert(not rep2["summary"]["ok"], "dangling relation must fail")
        _assert("C1" in _checks(rep2, "errors"), "expected a C1 dangling-pointer error")
        print("OK B: dangling to_node -> C1 error")

        # --- Case C: old Venus KB (no relations.jsonl) -> warning, no crash ---
        venus = ROOT / "knowledge_base_v04"
        if venus.exists() and (venus / "nodes.jsonl").exists():
            rep3 = graph.validate_graph(venus)
            _assert("C2" in _checks(rep3, "warnings"),
                    "old KB without relations.jsonl should emit a C2 warning")
            _assert(rep3["summary"]["ok"],
                    f"old Venus KB should pass (warnings only); errors={rep3['errors']}")
            print(f"OK C: Venus KB no relations.jsonl -> warning, ok "
                  f"(nodes={rep3['summary']['nodes']}, warns={rep3['summary']['warnings']})")
        else:
            # Synthetic stand-in for an old KB if the corpus is absent.
            kb3 = tmp / "kb_old"
            v04.ensure_kb_v04(kb3)
            _write_jsonl(kb3 / "nodes.jsonl", FIXTURE)
            rep3 = graph.validate_graph(kb3)
            _assert("C2" in _checks(rep3, "warnings"), "synthetic old KB should emit C2 warning")
            _assert(rep3["summary"]["ok"], "synthetic old KB should pass with warnings")
            print("OK C: synthetic old KB (no relations) -> warning, ok")

        # --- Case D: duplicate relation id -> C4 error ---
        kb4 = tmp / "kb_dup"
        v04.ensure_kb_v04(kb4)
        _write_jsonl(kb4 / "nodes.jsonl", FIXTURE)
        _write_jsonl(kb4 / graph.RELATIONS_FILE, [rel, dict(rel)])  # same relation_id twice
        rep4 = graph.validate_graph(kb4)
        _assert("C4" in _checks(rep4, "errors"), "expected C4 duplicate-id error")
        print("OK D: duplicate relation_id -> C4 error")

        # --- Case E: illegal state transition -> C8 error ---
        kb5 = tmp / "kb_transition"
        v04.ensure_kb_v04(kb5)
        _write_jsonl(kb5 / "nodes.jsonl", FIXTURE)
        _write_jsonl(kb5 / "review_state.jsonl", [{
            "review_state_id": "review_aaaaaaaaaaaa",
            "from_stage": "rejected", "to_stage": "supported",  # rejected is terminal
        }])
        rep5 = graph.validate_graph(kb5)
        _assert("C8" in _checks(rep5, "errors"), "expected C8 illegal-transition error")
        # A legal transition must NOT error.
        _write_jsonl(kb5 / "review_state.jsonl", [{
            "review_state_id": "review_bbbbbbbbbbbb",
            "from_stage": "candidate", "to_stage": "reviewed",
        }])
        rep5b = graph.validate_graph(kb5)
        _assert("C8" not in _checks(rep5b, "errors"), "legal transition must not error")
        print("OK E: rejected->supported -> C8 error; candidate->reviewed -> clean")

        # --- Case F: world_model leakage of non-accepted patch -> C9 error ---
        kb6 = tmp / "kb_leak"
        v04.ensure_kb_v04(kb6)
        _write_jsonl(kb6 / "nodes.jsonl", FIXTURE)
        leak_claim = "Unverified candidate claim that must stay out of conclusions."
        (kb6 / "world_model.md").write_text(
            "# World Model\n\n## 结论 (Conclusions)\n\n" + leak_claim + "\n",
            encoding="utf-8")
        _write_jsonl(kb6 / "mother_patch.jsonl", [{
            "mother_patch_id": "patch_cccccccccccc",
            "patch_status": "proposed",
            "add": [{"target": "world_model.candidate_hypotheses", "content": leak_claim}],
        }])
        rep6 = graph.validate_graph(kb6)
        _assert("C9" in _checks(rep6, "errors"), "expected C9 world_model-leakage error")
        print("OK F: non-accepted patch in conclusion section -> C9 error")

        print("\nALL OK: graph contract T0 smoke test passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
