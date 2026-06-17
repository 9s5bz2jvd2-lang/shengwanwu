#!/usr/bin/env python3
"""Stdlib-only smoke test for the Shengwanwu Loop V0.4 five-gate pipeline.

Run:  python3 -m tests.test_v04_smoke    (from repo root)
  or: python3 tests/test_v04_smoke.py

Uses a tiny synthetic distilled-JSONL fixture in a temp dir so it never
depends on the Venus corpus or the network.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Make the package importable when run as a plain script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shengwanwu_loop import v04  # noqa: E402

FIXTURE = [
    {"unit_id": "u1", "node_id": "u1_001", "node_type": "finding",
     "content": "The clouds are mainly concentrated sulfuric acid (H2SO4) with water as a secondary component; acidity is extreme.",
     "tags": ["composition", "H2SO4", "acidity"]},
    {"unit_id": "u1", "node_id": "u1_002", "node_type": "concept",
     "content": "Habitability of the cloud layer has been proposed, suggesting possible biogenic life despite the environment.",
     "tags": ["habitability", "life", "astrobiology"]},
    {"unit_id": "u1", "node_id": "u1_003", "node_type": "limitation",
     "content": "The stability of amino acids in concentrated sulfuric acid remains uncertain and is poorly constrained.",
     "tags": ["amino acid", "stability", "uncertain"]},
    {"unit_id": "u2", "node_id": "u2_001", "node_type": "result",
     "content": "Cloud-top temperature shows temporal variation with latitude and altitude, modulated by thermal tides.",
     "tags": ["cloud-top temperature", "temporal variation", "thermal tide"]},
    {"unit_id": "u2", "node_id": "u2_002", "node_type": "finding",
     "content": "A UV absorber of unknown identity affects the cloud-top albedo; its link to SO2 chemistry is debated.",
     "tags": ["UV_absorber", "SO2", "albedo"]},
]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="swwl_v04_smoke_"))
    try:
        src = tmp / "fixture.jsonl"
        src.write_text("\n".join(json.dumps(r) for r in FIXTURE) + "\n", encoding="utf-8")
        kb = tmp / "kb"
        out = tmp / "run"
        focus = "physical chemistry of Venus clouds"

        # Gate 1 + 2
        new_sources, new_nodes = v04.ingest_sources(kb, [str(src)], "distilled-jsonl")
        _assert(new_sources == 1, "expected 1 source")
        _assert(new_nodes == len(FIXTURE), f"expected {len(FIXTURE)} nodes, got {new_nodes}")

        # Idempotency: re-ingest same file -> no new nodes
        s2, n2 = v04.ingest_sources(kb, [str(src)], "distilled-jsonl")
        _assert(s2 == 0 and n2 == 0, "re-ingest should be idempotent")

        for name in v04.KB_FILES:
            _assert((kb / name).exists(), f"missing KB file {name}")

        # Gate 3
        gaps = v04.run_map(kb, out, focus, max_gaps=60)
        _assert(gaps, "no gaps produced")
        gap_types = {g["gap_type"] for g in gaps}
        _assert("contradiction" in gap_types, "expected a contradiction gap")
        for g in gaps:
            for field in ("gap_id", "gap_type", "operator", "description",
                          "related_nodes", "related_hypotheses", "derivation",
                          "evidence_boundary", "priority_score", "focus"):
                _assert(field in g, f"gap missing field {field}")
            _assert(g["gap_id"].startswith("gap_"), "bad gap_id prefix")

        # Gate 4a
        hyps = v04.run_hypothesis(kb, out, focus, max_per_gap=2)
        _assert(hyps, "no hypotheses produced")
        for h in hyps:
            for field in ("hypothesis_id", "gap_id", "claim", "minimal_explanation",
                          "source_nodes", "supporting_quotes", "reasoning_type",
                          "confidence", "validation_status", "proposed_validation"):
                _assert(field in h, f"hypothesis missing field {field}")
            _assert(h["hypothesis_id"].startswith("hyp_"), "bad hyp_id prefix")
            _assert(h["reasoning_type"] in {"verified", "inferred", "analogical", "speculative"},
                    "bad reasoning_type")

        # Lineage / mother-patch / review-state spine is created at generation time.
        for name in ("lineage.jsonl", "mother_patch.jsonl", "review_state.jsonl"):
            rows = v04.read_jsonl(out / name)
            _assert(len(rows) == len(hyps), f"{name} should have one row per hypothesis")
        _assert(v04.read_jsonl(out / "lineage.jsonl")[0]["lineage_id"].startswith("lin_"), "bad lineage_id prefix")
        _assert(v04.read_jsonl(out / "mother_patch.jsonl")[0]["mother_patch_id"].startswith("patch_"), "bad mother_patch_id prefix")
        _assert(v04.read_jsonl(out / "review_state.jsonl")[0]["review_state_id"].startswith("review_"), "bad review_state_id prefix")

        # Gate 4b — Six-Eyes
        vals = v04.run_validate(kb, out)
        _assert(len(vals) == len(hyps), "one validation per hypothesis expected")
        eyes_required = {"evidence_eye", "provenance_eye", "contradiction_eye",
                         "falsifiability_eye", "novelty_eye", "boundary_eye"}
        for v in vals:
            for field in ("validation_id", "hypothesis_id", "status", "evidence_level",
                          "supporting_nodes", "contradicting_nodes", "six_eyes",
                          "six_eyes_summary", "next_test"):
                _assert(field in v, f"validation missing field {field}")
            _assert(set(v["six_eyes"].keys()) == eyes_required, "six_eyes must have all 6 eyes")
            for eye in v["six_eyes"].values():
                _assert(eye["status"] in {"pass", "warn", "fail"}, "bad eye status")
            _assert(v["six_eyes_summary"] in {"pass", "needs_review", "reject"}, "bad summary")
            _assert(v["status"] in {"pending", "supported", "weakened", "rejected", "needs_data"},
                    "bad status")

        # Gate 5
        v04.run_return(kb, out, write_report=True, consolidate=True)
        for name in ("loop_state.json", "gaps.jsonl", "hypothesis_candidates.jsonl",
                     "validations.jsonl", "lineage.jsonl", "mother_patch.jsonl",
                     "review_state.jsonl", "validation_notes.md", "artifact_cards.jsonl",
                     "return_contract.md", "final_report.md"):
            p = out / name
            _assert(p.exists() and p.stat().st_size > 0, f"missing/empty run artifact {name}")
        _assert((kb / "world_model.md").read_text(encoding="utf-8").strip(), "world_model empty")

        print("OK: V0.4 smoke test passed")
        print(f"  sources={new_sources} nodes={new_nodes} gaps={len(gaps)} "
              f"hypotheses={len(hyps)} validations={len(vals)}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
