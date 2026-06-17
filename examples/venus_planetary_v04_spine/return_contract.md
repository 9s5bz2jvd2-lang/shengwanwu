# Return Contract (V0.4)

- loop_id: `venus_planetary_science_v04_spine`
- loop_version: v0.4
- focus: planetary science of Venus clouds: atmospheric dynamics, cloud microphysics, radiative balance, chemistry, observations, and habitability boundaries
- kb: `knowledge_base_v04`
- updated_at: 2026-06-17T12:16:03.557185+00:00

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
- gaps: `work/runs/venus_planetary_science_v04_spine/gaps.jsonl`
- hypotheses: `work/runs/venus_planetary_science_v04_spine/hypothesis_candidates.jsonl`
- validations: `work/runs/venus_planetary_science_v04_spine/validations.jsonl`
- lineage: `work/runs/venus_planetary_science_v04_spine/lineage.jsonl`
- mother_patch: `work/runs/venus_planetary_science_v04_spine/mother_patch.jsonl`
- review_state: `work/runs/venus_planetary_science_v04_spine/review_state.jsonl`
- validation notes: `work/runs/venus_planetary_science_v04_spine/validation_notes.md`
- final report: `work/runs/venus_planetary_science_v04_spine/final_report.md`

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
