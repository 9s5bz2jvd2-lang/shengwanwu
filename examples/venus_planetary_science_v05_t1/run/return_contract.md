# Return Contract (V0.4)

- loop_id: `v05_t1_20260624T171622Z`
- loop_version: v0.4
- focus: Venus planetary science
- kb: `knowledge_base_v05_t1_20260624T171622Z`
- updated_at: 2026-06-24T17:16:22.309767+00:00

## Gates completed
1. Input gate — distilled sources ingested.
2. Knowledge field gate — persistent KB updated.
3. Map gate — gap operators applied.
4. Generate+Validate gate — hypotheses + Six-Eyes validation.
5. Return gate — this contract + consolidation.

## Counts
- sources/nodes/gaps/hypotheses/validations: 6/175/80/94/94
- six_eyes_summary: {"needs_review": 66, "pass": 28}

## Key artifacts
- gaps: `work/runs/v05_t1_20260624T171622Z/gaps.jsonl`
- hypotheses: `work/runs/v05_t1_20260624T171622Z/hypothesis_candidates.jsonl`
- validations: `work/runs/v05_t1_20260624T171622Z/validations.jsonl`
- lineage: `work/runs/v05_t1_20260624T171622Z/lineage.jsonl`
- mother_patch: `work/runs/v05_t1_20260624T171622Z/mother_patch.jsonl`
- review_state: `work/runs/v05_t1_20260624T171622Z/review_state.jsonl`
- validation notes: `work/runs/v05_t1_20260624T171622Z/validation_notes.md`
- final report: `work/runs/v05_t1_20260624T171622Z/final_report.md`

## Evidence boundary
- All hypotheses are *candidates*, not verified scientific facts.
- Six-Eyes is a stdlib heuristic, not a full LLM review; `reject`/`needs_review` must be human-checked.
- `mother_patch.jsonl` is a proposed patch queue, not an automatic world-model rewrite.
- Distilled summaries are not full-text verification; confirm against primary literature.

## Next loop entry
1. Take `supported` hypotheses to a simulation/experiment/observation gate.
2. Record any rejected reconciliation as an anti-pattern (already consolidated).
3. Re-run map after adding new distilled sources.

## Do not repeat
- Do not re-ingest a source whose content hash is already in `sources.jsonl`.
- Do not promote a candidate hypothesis to a verified conclusion.
