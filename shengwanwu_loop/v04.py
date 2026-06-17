#!/usr/bin/env python3
"""Shengwanwu Loop V0.4 — local, stdlib-only, file/CLI five-gate workflow.

Five gates (Six-Eyes lives *inside* gate 4, it is not a sixth gate):

    1. Input gate          : accept already distilled JSONL / Markdown sources.
    2. Knowledge field gate : persistent knowledge_base/ JSONL store.
    3. Map gate             : Map / Gap Operators over the knowledge field.
    4. Generate+Validate    : hypotheses + Six-Eyes validation.
    5. Return gate          : report + idempotent return + light consolidation.

Design sentence:
    Distilled Source -> Persistent Field -> Gap Map -> Hypothesis Seed
        -> Six-Eyes Validation -> Crystallized Artifact -> Return Contract -> Next Loop

Everything here is conservative heuristics on Python stdlib only. Outputs are
*candidate* scientific hypotheses, never verified conclusions.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .cli import (
    append_jsonl,
    read_jsonl,
    sha256_file,
    stable_hash,
    utc_now,
    write_jsonl,
)

# ---------------------------------------------------------------------------
# Knowledge base schema (gate 2)
# ---------------------------------------------------------------------------

KB_FILES = [
    "sources.jsonl",
    "nodes.jsonl",
    "gaps.jsonl",
    "hypotheses.jsonl",
    "validations.jsonl",
    "lineage.jsonl",
    "mother_patch.jsonl",
    "review_state.jsonl",
    "anti_patterns.jsonl",
    "world_model.md",
]

# Loop state machine for the run directory.
V04_STATES = [
    "G1_INPUT",
    "G2_FIELD",
    "G3_MAP",
    "G4_GENERATE",
    "G4_VALIDATE",
    "G5_RETURN",
]


def ensure_kb_v04(kb: Path) -> None:
    """Create the persistent knowledge field with all V0.4 stores."""
    kb.mkdir(parents=True, exist_ok=True)
    for name in KB_FILES:
        p = kb / name
        if not p.exists():
            if name.endswith(".md"):
                p.write_text("# World Model\n\n(empty — populated on return/consolidate)\n", encoding="utf-8")
            else:
                p.touch()


def ensure_run_v04(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Vocabulary for Venus physical-chemistry gap operators
# ---------------------------------------------------------------------------

# Lexical signals reused across operators. All lower-cased substring tests.
WEAK_EVIDENCE_SIGNALS = [
    "uncertain", "unknown", "unresolved", "limitation", "limited",
    "not yet", "remains unclear", "poorly constrained", "speculative",
    "open question", "further", "needs", "tentative", "unconfirmed",
    "debated", "controvers", "hypothetical", "to be determined",
]

MECHANISM_TERMS = [
    "h2so4", "sulfuric acid", "so2", "sulfur dioxide", "h2o", "water",
    "uv absorber", "uv_absorber", "ultraviolet absorber", "cloud-top temperature",
    "cloud top", "aerosol", "amino acid", "heteropolymer", "panspermia",
    "temporal variation", "acidity", "water activity", "sulfonation",
    "sulfation", "deamidation", "convection", "thermal tide",
]

CONTRADICTION_AXES = [
    # (axis label, terms_a, terms_b) — co-mention of both sides flags tension.
    ("habitability_vs_acidity", ["habitab", "life", "biosignature", "biogenic", "living"],
     ["concentrated sulfuric", "sulfuric acid", "acidity", "low water activity", "dehydrat"]),
    ("amino_acid_stability", ["amino acid", "biomolecule", "peptide", "biogenic"],
     ["decompos", "unstable", "degrad", "destroy", "deamidation", "react"]),
    ("carbon_free_heteropolymer", ["carbon-free", "heteropolymer", "non-carbon", "silicon", "phosphorus"],
     ["unknown", "speculative", "no known", "untested", "hypothetical"]),
]

TEMPORAL_TERMS = [
    "temporal variation", "temporal", "cloud-top temperature", "cloud top temperature",
    "brightness temperature", "solar", "altitude", "latitude", "latitudinal",
    "diurnal", "long-term", "decadal", "seasonal", "thermal tide", "year-to-year",
]

# Buckets for the physical_chemistry_mechanism operator.
PC_BUCKETS = {
    "composition": ["composition", "h2so4", "sulfuric acid", "h2o", "so2", "concentration", "weight percent"],
    "acidity": ["acidity", "ph", "water activity", "concentrated", "dehydrat", "hygroscop"],
    "uv": ["uv absorber", "uv_absorber", "ultraviolet", "uv ", "albedo", "absorption"],
    "temperature": ["temperature", "thermal", "cloud-top temperature", "brightness temperature"],
    "aerosol": ["aerosol", "particle", "droplet", "microphysics", "mode 1", "mode 2", "mode 3"],
    "chemical_stability": ["stability", "kinetic", "decompos", "degrad", "reaction", "sulfonation", "sulfation", "deamidation"],
}

PLANETARY_BUCKETS = {
    "atmospheric_dynamics": [
        "atmosphere", "atmospheric", "dynamics", "circulation", "super-rotation",
        "superrotation", "thermal tide", "wave", "latitude", "altitude", "diurnal",
        "temporal variation", "vertical", "cloud-top temperature",
    ],
    "cloud_microphysics": [
        "cloud", "aerosol", "droplet", "particle", "microphysics", "mode 1",
        "mode 2", "mode 3", "haze", "opacity", "cloud deck",
    ],
    "radiative_balance": [
        "radiative", "radiation", "uv", "ultraviolet", "albedo", "absorber",
        "solar", "infrared", "brightness temperature", "thermal emission",
    ],
    "atmospheric_chemistry": [
        "chemistry", "chemical", "so2", "h2so4", "sulfur", "sulfuric acid",
        "composition", "photochemistry", "water", "acidity",
    ],
    "observational_constraints": [
        "observation", "observed", "spacecraft", "mission", "venus express", "akatsuki",
        "spectra", "spectral", "measurement", "remote sensing", "ground-based",
    ],
    "habitability_boundaries": [
        "habitability", "habitab", "life", "biosignature", "biogenic", "amino acid",
        "biomolecule", "water activity", "stability", "acid",
    ],
}


def _text_of(node: dict) -> str:
    return (node.get("content", "") or "").lower()


def _short(text: str, n: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _gap_id(seed: str) -> str:
    return "gap_" + stable_hash(seed)[:12]


def _hyp_id(seed: str) -> str:
    return "hyp_" + stable_hash(seed)[:12]


def _val_id(seed: str) -> str:
    return "val_" + stable_hash(seed)[:12]


# ---------------------------------------------------------------------------
# Gate 1 + 2: ingest distilled sources into the knowledge field
# ---------------------------------------------------------------------------


def _normalize_distilled_row(row: dict, source_path: Path, source_id: str, idx: int) -> dict:
    """Normalize one distilled JSONL row into a KB node."""
    unit_id = row.get("unit_id") or source_id
    node_id = row.get("node_id") or f"{unit_id}_{idx:04d}"
    content = row.get("content", "")
    tags = row.get("tags", []) or []
    return {
        "node_id": node_id,
        "unit_id": unit_id,
        "source_id": source_id,
        "source_path": str(source_path),
        "node_type": row.get("node_type", "concept"),
        "content": content,
        "tags": tags,
        "evidence_span": {"unit_id": unit_id, "quote": _short(content, 500)},
        "confidence": "distilled",
        "created_at": utc_now(),
    }


def _markdown_to_rows(path: Path) -> List[dict]:
    """Very light Markdown fallback: each bullet / numbered / long line -> a node."""
    rows: List[dict] = []
    heading = path.stem
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip() or heading
            continue
        if line.startswith(("-", "*", "+")):
            content = line.lstrip("-*+ ").strip()
        elif re.match(r"^\d+[.)]\s+", line):
            content = re.sub(r"^\d+[.)]\s+", "", line).strip()
        elif len(line) >= 28:
            content = line
        else:
            continue
        if content:
            rows.append({"node_type": "concept", "content": content, "tags": [], "heading": heading})
    return rows


def ingest_sources(kb: Path, inputs: Sequence[str], mode: str) -> Tuple[int, int]:
    """Gate 1 + 2. Returns (new_sources, new_nodes). Idempotent on content hash."""
    ensure_kb_v04(kb)
    sources_path = kb / "sources.jsonl"
    nodes_path = kb / "nodes.jsonl"
    known_hashes = {r.get("content_hash") for r in read_jsonl(sources_path)}
    known_node_ids = {n.get("node_id") for n in read_jsonl(nodes_path)}

    new_sources = 0
    new_nodes = 0
    for item in inputs:
        path = Path(item)
        if not path.is_file():
            print(f"[skip] not found: {path}")
            continue
        content_hash = sha256_file(path)
        if content_hash in known_hashes:
            print(f"[skip] already ingested (hash match): {path}")
            continue
        source_id = f"src_{content_hash[:12]}"

        if mode == "distilled-jsonl" or (mode == "auto" and path.suffix.lower() == ".jsonl"):
            raw_rows = read_jsonl(path)
        else:
            raw_rows = _markdown_to_rows(path)

        nodes: List[dict] = []
        for idx, row in enumerate(raw_rows, 1):
            node = _normalize_distilled_row(row, path, source_id, idx)
            if node["node_id"] in known_node_ids:
                # Keep node ids unique across the whole field.
                node["node_id"] = f"{node['node_id']}_{source_id[-6:]}"
            known_node_ids.add(node["node_id"])
            nodes.append(node)

        # Derive the unit ids represented in this file.
        units = sorted({n["unit_id"] for n in nodes})
        append_jsonl(nodes_path, nodes)
        append_jsonl(sources_path, [{
            "source_id": source_id,
            "path": str(path),
            "mode": mode,
            "hash_algorithm": "sha256",
            "content_hash": content_hash,
            "unit_ids": units,
            "node_count": len(nodes),
            "processed_at": utc_now(),
        }])
        known_hashes.add(content_hash)
        new_sources += 1
        new_nodes += len(nodes)
        print(f"[add] {path} -> {len(nodes)} nodes (units: {', '.join(units) or 'n/a'})")

    return new_sources, new_nodes


# ---------------------------------------------------------------------------
# Gate 3: Map / Gap operators
# ---------------------------------------------------------------------------


def _priority(gap_type: str, related: List[str], hits: int) -> float:
    base = {
        "contradiction": 0.85,
        "physical_chemistry_mechanism": 0.8,
        "missing_link": 0.65,
        "temporal_drift": 0.6,
        "weak_evidence": 0.5,
        "cross_domain_transfer": 0.55,
        "failed_hypothesis": 0.4,
        "planetary_science_synthesis": 0.75,
    }.get(gap_type, 0.4)
    # More related nodes and more lexical hits -> slightly higher priority.
    score = base + 0.05 * min(len(related), 3) + 0.02 * min(hits, 5)
    return round(min(score, 0.99), 3)


def _make_gap(gap_type: str, operator: str, description: str, related: List[str],
              derivation: str, boundary: str, focus: str, hits: int = 1) -> dict:
    related = list(dict.fromkeys(related))  # stable unique
    seed = f"{gap_type}|{operator}|{'|'.join(sorted(related))}|{description[:80]}"
    return {
        "gap_id": _gap_id(seed),
        "gap_type": gap_type,
        "operator": operator,
        "description": description,
        "related_nodes": related,
        "related_hypotheses": [],
        "derivation": derivation,
        "evidence_boundary": boundary,
        "priority_score": _priority(gap_type, related, hits),
        "focus": focus,
        "created_at": utc_now(),
    }


def op_weak_evidence(nodes: List[dict], focus: str) -> List[dict]:
    gaps: List[dict] = []
    for n in nodes:
        t = _text_of(n)
        matched = [s for s in WEAK_EVIDENCE_SIGNALS if s in t]
        if matched:
            gaps.append(_make_gap(
                "weak_evidence", "WeakEvidenceOperator",
                f"Claim rests on weak / incomplete evidence: {_short(n.get('content',''))}",
                [n["node_id"]],
                f"Node contains uncertainty markers ({', '.join(matched[:4])}).",
                "The source itself flags the claim as uncertain; no firmer evidence is in the field.",
                focus, hits=len(matched),
            ))
    return gaps


def op_missing_link(nodes: List[dict], focus: str) -> List[dict]:
    """Mechanism / causal-chain nodes that mention >=2 mechanism terms but are not
    already an explicit relationship/mechanism node get flagged as a missing link."""
    gaps: List[dict] = []
    for n in nodes:
        t = _text_of(n)
        terms = [m for m in MECHANISM_TERMS if m in t]
        if len(terms) >= 2 and n.get("node_type") not in {"relationship", "mechanism", "formula"}:
            gaps.append(_make_gap(
                "missing_link", "MissingLinkOperator",
                f"Mechanistic terms co-occur without an explicit causal link: {_short(n.get('content',''))}",
                [n["node_id"]],
                f"Node mentions multiple mechanism terms ({', '.join(sorted(set(terms))[:5])}) but states no explicit mechanism edge.",
                "The causal chain connecting these terms is not established in the field; it is a candidate link.",
                focus, hits=len(terms),
            ))
    return gaps


def op_contradiction(nodes: List[dict], focus: str) -> List[dict]:
    gaps: List[dict] = []
    for axis, terms_a, terms_b in CONTRADICTION_AXES:
        hits_a = [n for n in nodes if any(x in _text_of(n) for x in terms_a)]
        hits_b = [n for n in nodes if any(x in _text_of(n) for x in terms_b)]
        if hits_a and hits_b:
            related = [hits_a[0]["node_id"], hits_b[0]["node_id"]]
            # pull a few more representative nodes
            for extra in (hits_a[1:2] + hits_b[1:2]):
                related.append(extra["node_id"])
            gaps.append(_make_gap(
                "contradiction", "ContradictionOperator",
                f"Tension on axis '{axis}': one set of nodes asserts {axis.split('_')[0]} while another asserts conditions that work against it.",
                related,
                f"Co-mention of opposing signals ({terms_a[0]} vs {terms_b[0]}) across distinct nodes.",
                "Whether these are truly incompatible or reconciled by a hidden boundary condition is unresolved.",
                focus, hits=min(len(hits_a), len(hits_b)),
            ))
    return gaps


def op_temporal_drift(nodes: List[dict], focus: str) -> List[dict]:
    gaps: List[dict] = []
    for n in nodes:
        t = _text_of(n)
        terms = [m for m in TEMPORAL_TERMS if m in t]
        if len(terms) >= 2:
            gaps.append(_make_gap(
                "temporal_drift", "TemporalDriftOperator",
                f"Temporal / spatial variability may not be explained by a stable mechanism: {_short(n.get('content',''))}",
                [n["node_id"]],
                f"Node references variability dimensions ({', '.join(sorted(set(terms))[:5])}).",
                "The driver of the observed temporal/spatial drift is not pinned to a mechanism in the field.",
                focus, hits=len(terms),
            ))
    return gaps


def op_physical_chemistry_mechanism(nodes: List[dict], focus: str) -> List[dict]:
    """Combine nodes from different physical-chemistry buckets into mechanism gaps."""
    bucket_nodes: Dict[str, List[dict]] = {k: [] for k in PC_BUCKETS}
    for n in nodes:
        t = _text_of(n)
        for bucket, terms in PC_BUCKETS.items():
            if any(x in t for x in terms):
                bucket_nodes[bucket].append(n)

    gaps: List[dict] = []
    # Pair complementary buckets to form mechanism candidates.
    pairs = [
        ("composition", "acidity"),
        ("acidity", "chemical_stability"),
        ("uv", "composition"),
        ("temperature", "aerosol"),
        ("composition", "chemical_stability"),
    ]
    for a, b in pairs:
        na, nb = bucket_nodes.get(a, []), bucket_nodes.get(b, [])
        if na and nb:
            related = [na[0]["node_id"], nb[0]["node_id"]]
            if len(na) > 1:
                related.append(na[1]["node_id"])
            gaps.append(_make_gap(
                "physical_chemistry_mechanism", "PhysicalChemistryMechanismOperator",
                f"Mechanism linking {a} and {b} of Venus cloud chemistry is not explicit in the field.",
                related,
                f"Field has separate {a} nodes and {b} nodes but no node ties them into one physical-chemistry mechanism.",
                "The proposed mechanism is a candidate synthesis across distilled nodes, not an established result.",
                focus, hits=min(len(na), len(nb)),
            ))
    return gaps


def op_planetary_science_synthesis(nodes: List[dict], focus: str) -> List[dict]:
    """Cross-link planetary-science buckets: dynamics, clouds, radiation,
    chemistry, observations, and habitability boundaries.

    This operator is intentionally broader than the physical-chemistry operator.
    It fires only when the focus asks for a planetary / Venus / atmospheric
    reading, so ordinary V0.4 runs keep their old behavior.
    """
    focus_l = focus.lower()
    if not any(k in focus_l for k in ["planetary", "planet", "atmospheric", "venus", "行星"]):
        return []

    bucket_nodes: Dict[str, List[dict]] = {k: [] for k in PLANETARY_BUCKETS}
    for n in nodes:
        t = _text_of(n)
        for bucket, terms in PLANETARY_BUCKETS.items():
            if any(x in t for x in terms):
                bucket_nodes[bucket].append(n)

    pairs = [
        ("atmospheric_dynamics", "cloud_microphysics"),
        ("radiative_balance", "cloud_microphysics"),
        ("atmospheric_chemistry", "cloud_microphysics"),
        ("observational_constraints", "atmospheric_dynamics"),
        ("habitability_boundaries", "atmospheric_chemistry"),
        ("observational_constraints", "radiative_balance"),
    ]
    descriptions = {
        ("atmospheric_dynamics", "cloud_microphysics"):
            "How Venus atmospheric dynamics reorganize cloud microphysics is not explicit in the field.",
        ("radiative_balance", "cloud_microphysics"):
            "The coupling between radiative balance, UV/IR signatures, and cloud microphysics is under-specified.",
        ("atmospheric_chemistry", "cloud_microphysics"):
            "The planetary-scale link between atmospheric chemistry and cloud particle populations is not explicit.",
        ("observational_constraints", "atmospheric_dynamics"):
            "Observation-to-dynamics inference is present but not closed as a planetary-science chain.",
        ("habitability_boundaries", "atmospheric_chemistry"):
            "Habitability boundary claims need to be tied to planetary atmospheric chemistry constraints.",
        ("observational_constraints", "radiative_balance"):
            "Observed radiative signals need a clearer chain to planetary energy-balance interpretation.",
    }
    gaps: List[dict] = []
    for a, b in pairs:
        na, nb = bucket_nodes.get(a, []), bucket_nodes.get(b, [])
        if not (na and nb):
            continue
        related = [na[0]["node_id"], nb[0]["node_id"]]
        if len(na) > 1:
            related.append(na[1]["node_id"])
        if len(nb) > 1:
            related.append(nb[1]["node_id"])
        gaps.append(_make_gap(
            "planetary_science_synthesis", "PlanetaryScienceSynthesisOperator",
            descriptions.get((a, b), f"Planetary-science synthesis gap linking {a} and {b}."),
            related,
            f"Field has {a} nodes and {b} nodes, but no distilled node closes the planetary-scale causal chain between them.",
            "Planetary synthesis candidate: evidence must remain traceable to observations, dynamics, chemistry, and scale boundaries; do not promote without primary literature review.",
            focus, hits=min(len(na), len(nb)),
        ))
    return gaps


GAP_OPERATORS = [
    op_contradiction,
    op_planetary_science_synthesis,
    op_physical_chemistry_mechanism,
    op_missing_link,
    op_temporal_drift,
    op_weak_evidence,
]


def build_gaps_v04(nodes: List[dict], focus: str, max_gaps: int = 60) -> List[dict]:
    gaps: List[dict] = []
    for op in GAP_OPERATORS:
        gaps.extend(op(nodes, focus))
    # Deduplicate by gap_id, keep highest priority first.
    seen: Dict[str, dict] = {}
    for g in gaps:
        if g["gap_id"] not in seen:
            seen[g["gap_id"]] = g
    ordered = sorted(seen.values(), key=lambda g: -g["priority_score"])
    return ordered[:max_gaps]


# ---------------------------------------------------------------------------
# Gate 4a: hypothesis generation
# ---------------------------------------------------------------------------


def _reasoning_type_for(gap: dict) -> str:
    return {
        "contradiction": "inferred",
        "physical_chemistry_mechanism": "inferred",
        "missing_link": "inferred",
        "temporal_drift": "inferred",
        "cross_domain_transfer": "analogical",
        "weak_evidence": "speculative",
        "failed_hypothesis": "speculative",
        "planetary_science_synthesis": "inferred",
    }.get(gap.get("gap_type"), "speculative")


def _proposed_validation_for(gap: dict) -> List[str]:
    gt = gap.get("gap_type")
    if gt == "contradiction":
        return ["literature_check", "experiment"]
    if gt == "physical_chemistry_mechanism":
        return ["simulation", "experiment"]
    if gt == "temporal_drift":
        return ["observation", "simulation"]
    if gt == "planetary_science_synthesis":
        return ["observation", "simulation", "literature_check"]
    if gt == "missing_link":
        return ["literature_check", "simulation"]
    return ["literature_check"]


def hypotheses_from_gap(gap: dict, nodes_by_id: Dict[str, dict], focus: str,
                        max_per_gap: int) -> List[dict]:
    related = [nodes_by_id.get(nid) for nid in gap.get("related_nodes", [])]
    related = [n for n in related if n]
    quotes = [_short(n.get("content", ""), 200) for n in related[:3]]
    src_nodes = [n["node_id"] for n in related]
    reasoning = _reasoning_type_for(gap)

    gt = gap.get("gap_type")
    templates: List[Tuple[str, str]] = []
    if gt == "contradiction":
        templates.append((
            f"A hidden boundary condition (local concentration, micro-environment, or transient state) may reconcile the apparent tension flagged by gap {gap['gap_id']}.",
            "If a narrow physical/chemical regime exists where both observations hold, the contradiction dissolves without either being wrong.",
        ))
        templates.append((
            f"The two sides of gap {gap['gap_id']} may refer to different spatial or temporal layers of the Venus cloud deck rather than the same parcel.",
            "Stratifying the claims by altitude/latitude/time could remove the contradiction.",
        ))
    elif gt == "physical_chemistry_mechanism":
        templates.append((
            f"A physical-chemistry pathway coupling the buckets named in gap {gap['gap_id']} may govern the observed cloud property.",
            "A single coupled mechanism (composition -> acidity/temperature -> stability/UV signature) would parsimoniously connect the separate distilled observations.",
        ))
        templates.append((
            f"The property in gap {gap['gap_id']} may be rate-limited by one step in the coupled pathway rather than by equilibrium composition.",
            "If kinetics dominate, equilibrium-only reasoning would mispredict the observed state.",
        ))
    elif gt == "planetary_science_synthesis":
        templates.append((
            f"A planetary-scale coupling across the domains named in gap {gap['gap_id']} may explain the Venus cloud behavior better than treating each domain separately.",
            "Planetary interpretation requires connecting observations, atmospheric dynamics, radiative balance, chemistry, and cloud microphysics into one traceable causal chain.",
        ))
        templates.append((
            f"The apparent signal in gap {gap['gap_id']} may be controlled by a scale mismatch: local chemistry or microphysics is being interpreted as a planet-wide atmospheric pattern.",
            "Testing the scale boundary prevents over-promoting a local mechanism into a planetary conclusion.",
        ))
    elif gt == "missing_link":
        templates.append((
            f"An unobserved intermediate species or step may causally connect the co-mentioned terms in gap {gap['gap_id']}.",
            "Positing one mediating intermediate is the minimal way to turn co-occurrence into a causal chain.",
        ))
    elif gt == "temporal_drift":
        templates.append((
            f"The temporal/spatial drift in gap {gap['gap_id']} may be driven by a periodic dynamical forcing (e.g. thermal tide or wave) modulating cloud-top properties.",
            "A periodic driver would explain systematic variation without invoking compositional change.",
        ))
    else:  # weak_evidence / fallback
        templates.append((
            f"The weakly-evidenced claim behind gap {gap['gap_id']} could be tightened by a targeted measurement; as stated it is provisional.",
            "Naming the missing measurement converts an uncertainty marker into a testable next step.",
        ))

    out: List[dict] = []
    for i, (claim, minimal) in enumerate(templates[:max_per_gap]):
        seed = f"{gap['gap_id']}|{claim}"
        out.append({
            "hypothesis_id": _hyp_id(seed),
            "gap_id": gap["gap_id"],
            "claim": claim,
            "minimal_explanation": minimal,
            "source_nodes": src_nodes,
            "supporting_quotes": quotes,
            "reasoning_type": reasoning,
            "confidence": round(min(0.2 + gap.get("priority_score", 0.5) * 0.4, 0.6), 3),
            "validation_status": "pending",
            "proposed_validation": _proposed_validation_for(gap),
            "focus": focus,
            "created_at": utc_now(),
        })
    return out


# ---------------------------------------------------------------------------
# Gate 4b: Six-Eyes validation
# ---------------------------------------------------------------------------


def _eye(status: str, notes: str) -> dict:
    return {"status": status, "notes": notes}


def six_eyes(hyp: dict, gap: Optional[dict], nodes_by_id: Dict[str, dict],
             all_hypotheses: List[dict], anti_patterns: List[dict]) -> dict:
    """Return (six_eyes dict, summary, status, evidence_level, supporting, contradicting)."""
    src_nodes = [nodes_by_id.get(n) for n in hyp.get("source_nodes", [])]
    src_nodes = [n for n in src_nodes if n]
    n_src = len(src_nodes)
    reasoning = hyp.get("reasoning_type", "speculative")

    # --- evidence_eye: is there grounding in the field? ---
    if n_src >= 2:
        evidence_eye = _eye("pass", f"Anchored to {n_src} distilled nodes.")
    elif n_src == 1:
        evidence_eye = _eye("warn", "Anchored to a single node; grounding is thin.")
    else:
        evidence_eye = _eye("fail", "No source nodes in the field support this claim.")

    # --- provenance_eye: can we trace quotes back to units? ---
    have_quotes = bool(hyp.get("supporting_quotes"))
    units = sorted({n.get("unit_id") for n in src_nodes if n.get("unit_id")})
    if have_quotes and units:
        provenance_eye = _eye("pass", f"Quotes trace to units: {', '.join(units)}.")
    elif units:
        provenance_eye = _eye("warn", "Source units known but no quote captured.")
    else:
        provenance_eye = _eye("fail", "No provenance to a distilled source unit.")

    # --- contradiction_eye: does the underlying gap encode a real tension? ---
    if gap and gap.get("gap_type") == "contradiction":
        contradiction_eye = _eye("warn", "Built on an unresolved contradiction; reconciliation is itself unproven.")
    elif gap:
        contradiction_eye = _eye("pass", "No internal contradiction detected among source nodes.")
    else:
        contradiction_eye = _eye("warn", "Originating gap not found; cannot check for tension.")

    # --- falsifiability_eye: is there a concrete proposed test? ---
    proposed = hyp.get("proposed_validation", [])
    if proposed:
        falsifiability_eye = _eye("pass", f"Falsifiable via: {', '.join(proposed)}.")
    else:
        falsifiability_eye = _eye("fail", "No proposed validation; not yet falsifiable.")

    # --- novelty_eye: distinct from other hypotheses / known anti-patterns? ---
    claim_norm = re.sub(r"\s+", " ", hyp.get("claim", "").lower()).strip()
    dup = sum(1 for h in all_hypotheses
              if h.get("hypothesis_id") != hyp.get("hypothesis_id")
              and re.sub(r"\s+", " ", h.get("claim", "").lower()).strip() == claim_norm)
    anti_hit = any(ap.get("claim_norm") == claim_norm for ap in anti_patterns)
    if anti_hit:
        novelty_eye = _eye("fail", "Matches a recorded anti-pattern (previously rejected).")
    elif dup:
        novelty_eye = _eye("warn", "Near-duplicate of another candidate hypothesis.")
    else:
        novelty_eye = _eye("pass", "No duplicate or anti-pattern match.")

    # --- boundary_eye: does the claim respect its evidence boundary? ---
    claim_l = hyp.get("claim", "").lower()
    posits_unobserved = any(k in claim_l for k in
                            ["unobserved", "hidden", "intermediate", "may be driven", "periodic"])
    if reasoning == "speculative":
        boundary_eye = _eye("warn", "Speculative reasoning; must stay framed as a candidate.")
    elif reasoning == "analogical":
        boundary_eye = _eye("warn", "Analogical transfer; boundary of the analogy is untested.")
    elif posits_unobserved and n_src <= 1:
        boundary_eye = _eye("warn", "Posits an unobserved entity/driver from a single node; boundary is thin.")
    else:
        boundary_eye = _eye("pass", "Inference stays within the distilled evidence boundary.")

    eyes = {
        "evidence_eye": evidence_eye,
        "provenance_eye": provenance_eye,
        "contradiction_eye": contradiction_eye,
        "falsifiability_eye": falsifiability_eye,
        "novelty_eye": novelty_eye,
        "boundary_eye": boundary_eye,
    }

    statuses = [e["status"] for e in eyes.values()]
    n_fail = statuses.count("fail")
    n_warn = statuses.count("warn")

    if n_fail >= 1:
        summary = "reject"
    elif n_warn >= 2:
        summary = "needs_review"
    else:
        summary = "pass"

    # Map summary -> hypothesis validation_status.
    if summary == "reject":
        status = "rejected"
    elif summary == "needs_review":
        status = "needs_data" if n_warn >= 4 else "weakened"
    else:
        status = "supported"

    # Evidence level mirrors reasoning but is capped by grounding.
    if n_src == 0:
        evidence_level = "speculative"
    else:
        evidence_level = reasoning

    supporting = [n["node_id"] for n in src_nodes]
    contradicting: List[str] = []
    if gap and gap.get("gap_type") == "contradiction":
        # the gap's own related nodes are the contradicting pair
        contradicting = [nid for nid in gap.get("related_nodes", []) if nid not in supporting]

    next_test = proposed[0] if proposed else "literature_check"
    next_test_text = {
        "experiment": "Design a lab experiment under simulated Venus cloud conditions to probe the claim.",
        "simulation": "Run a physical-chemistry / dynamical simulation to test the proposed mechanism.",
        "observation": "Seek observational data (spacecraft / ground-based) that would discriminate the claim.",
        "literature_check": "Verify against full-text primary literature beyond the distilled summary.",
    }.get(next_test, "Verify against primary literature.")

    return {
        "six_eyes": eyes,
        "six_eyes_summary": summary,
        "status": status,
        "evidence_level": evidence_level,
        "supporting_nodes": supporting,
        "contradicting_nodes": contradicting,
        "next_test": next_test_text,
    }


def validate_hypotheses(hypotheses: List[dict], gaps_by_id: Dict[str, dict],
                        nodes_by_id: Dict[str, dict],
                        anti_patterns: List[dict]) -> List[dict]:
    validations: List[dict] = []
    for hyp in hypotheses:
        gap = gaps_by_id.get(hyp.get("gap_id"))
        result = six_eyes(hyp, gap, nodes_by_id, hypotheses, anti_patterns)
        seed = f"{hyp['hypothesis_id']}|{result['six_eyes_summary']}"
        validations.append({
            "validation_id": _val_id(seed),
            "hypothesis_id": hyp["hypothesis_id"],
            "gap_id": hyp.get("gap_id"),
            "status": result["status"],
            "evidence_level": result["evidence_level"],
            "supporting_nodes": result["supporting_nodes"],
            "contradicting_nodes": result["contradicting_nodes"],
            "six_eyes": result["six_eyes"],
            "six_eyes_summary": result["six_eyes_summary"],
            "next_test": result["next_test"],
            "created_at": utc_now(),
        })
    return validations


# ---------------------------------------------------------------------------
# Lineage / mother-patch / review-state spine
# ---------------------------------------------------------------------------


def _upsert_jsonl(path: Path, rows: Iterable[dict], key: str) -> int:
    """Upsert JSONL rows by a stable key and rewrite the file deterministically."""
    rows = list(rows)
    if not rows:
        return 0
    merged: Dict[str, dict] = {}
    for row in read_jsonl(path):
        k = row.get(key)
        if k:
            merged[k] = row
    changed = 0
    for row in rows:
        k = row.get(key)
        if not k:
            continue
        if merged.get(k) != row:
            changed += 1
        merged[k] = row
    write_jsonl(path, merged.values())
    return changed


def _lineage_rows(hypotheses: List[dict], gaps_by_id: Dict[str, dict],
                  nodes_by_id: Dict[str, dict]) -> List[dict]:
    rows: List[dict] = []
    for h in hypotheses:
        gap = gaps_by_id.get(h.get("gap_id"), {})
        nodes = [nodes_by_id.get(nid) for nid in h.get("source_nodes", [])]
        nodes = [n for n in nodes if n]
        source_ids = sorted({n.get("source_id") for n in nodes if n.get("source_id")})
        unit_ids = sorted({n.get("unit_id") for n in nodes if n.get("unit_id")})
        lineage_id = "lin_" + stable_hash(h.get("hypothesis_id", ""))[:12]
        rows.append({
            "lineage_id": lineage_id,
            "hypothesis_id": h.get("hypothesis_id"),
            "gap_id": h.get("gap_id"),
            "gap_type": gap.get("gap_type"),
            "operator": gap.get("operator"),
            "source_ids": source_ids,
            "unit_ids": unit_ids,
            "source_nodes": h.get("source_nodes", []),
            "supporting_quotes": h.get("supporting_quotes", []),
            "gap_derivation": gap.get("derivation"),
            "evidence_boundary": gap.get("evidence_boundary"),
            "causal_chain": ["source", "node", "gap", "hypothesis"],
            "created_at": h.get("created_at") or utc_now(),
        })
    return rows


def _mother_patch_rows(hypotheses: List[dict], gaps_by_id: Dict[str, dict],
                       validations_by_hyp: Optional[Dict[str, dict]] = None) -> List[dict]:
    validations_by_hyp = validations_by_hyp or {}
    rows: List[dict] = []
    for h in hypotheses:
        hid = h.get("hypothesis_id")
        gap = gaps_by_id.get(h.get("gap_id"), {})
        v = validations_by_hyp.get(hid, {})
        status = v.get("status") or h.get("validation_status", "pending")
        summary = v.get("six_eyes_summary", "pending")
        review_gate = "human_review_required"
        if status == "supported":
            review_gate = "eligible_for_human_promotion"
        elif status == "rejected":
            review_gate = "do_not_promote_record_as_antipattern"
        patch_id = "patch_" + stable_hash(str(hid))[:12]
        rows.append({
            "mother_patch_id": patch_id,
            "hypothesis_id": hid,
            "gap_id": h.get("gap_id"),
            "patch_status": "proposed",
            "review_gate": review_gate,
            "delete": [],
            "weaken": ([] if status == "supported" else [{
                "target": "hypothesis_claim",
                "reason": f"Six-Eyes status is {status}; keep claim candidate-level.",
            }]),
            "add": [
                {"target": "world_model.open_questions",
                 "content": gap.get("description", h.get("claim", ""))},
                {"target": "world_model.candidate_hypotheses",
                 "content": h.get("claim", "")},
            ],
            "demote_fact_to_interpretation": [],
            "promote_gap_to_question": [{
                "gap_id": h.get("gap_id"),
                "question": gap.get("description", ""),
            }] if h.get("gap_id") else [],
            "rollback_condition": v.get("next_test") or "Reject or downgrade if primary literature, simulation, observation, or experiment contradicts the candidate.",
            "evidence_debt": h.get("proposed_validation", []),
            "six_eyes_summary": summary,
            "updated_at": utc_now(),
        })
    return rows


def _review_stage(status: str, summary: str) -> str:
    if status == "supported" and summary == "pass":
        return "supported"
    if status == "rejected" or summary == "reject":
        return "rejected"
    if status in {"weakened", "needs_data"} or summary == "needs_review":
        return "contested"
    return "candidate"


def _review_state_rows(hypotheses: List[dict],
                       validations_by_hyp: Optional[Dict[str, dict]] = None) -> List[dict]:
    validations_by_hyp = validations_by_hyp or {}
    rows: List[dict] = []
    for h in hypotheses:
        hid = h.get("hypothesis_id")
        v = validations_by_hyp.get(hid, {})
        status = v.get("status") or h.get("validation_status", "pending")
        summary = v.get("six_eyes_summary", "pending")
        stage = _review_stage(status, summary)
        rows.append({
            "review_state_id": "review_" + stable_hash(str(hid))[:12],
            "hypothesis_id": hid,
            "gap_id": h.get("gap_id"),
            "stage": stage,
            "allowed_transitions": {
                "candidate": ["contested", "reviewed", "rejected"],
                "contested": ["reviewed", "rejected"],
                "reviewed": ["supported", "rejected"],
                "supported": ["contested"],
                "rejected": [],
            }.get(stage, []),
            "validation_status": status,
            "six_eyes_summary": summary,
            "human_review_required": stage not in {"rejected"},
            "notes": "Auto-derived from V0.4 heuristics; human must approve any world_model patch.",
            "updated_at": utc_now(),
        })
    return rows


# ---------------------------------------------------------------------------
# Run-state helpers
# ---------------------------------------------------------------------------


def write_loop_state(out: Path, kb: Path, state: str, completed: List[str],
                     focus: str, counts: Dict[str, int], next_action: str) -> None:
    data = {
        "loop_id": out.name,
        "loop_version": "v0.4",
        "current_state": state,
        "completed_states": completed,
        "kb_path": str(kb),
        "focus": focus,
        "counts": counts,
        "artifacts": {
            "gaps": str(out / "gaps.jsonl"),
            "hypothesis_candidates": str(out / "hypothesis_candidates.jsonl"),
            "validations": str(out / "validations.jsonl"),
            "lineage": str(out / "lineage.jsonl"),
            "mother_patch": str(out / "mother_patch.jsonl"),
            "review_state": str(out / "review_state.jsonl"),
            "validation_notes": str(out / "validation_notes.md"),
            "artifact_cards": str(out / "artifact_cards.jsonl"),
            "return_contract": str(out / "return_contract.md"),
            "final_report": str(out / "final_report.md"),
        },
        "next_action": next_action,
        "updated_at": utc_now(),
    }
    (out / "loop_state.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def read_loop_state(out: Path) -> Optional[dict]:
    p = out / "loop_state.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Gate 3 command
# ---------------------------------------------------------------------------


def run_map(kb: Path, out: Path, focus: str, max_gaps: int) -> List[dict]:
    ensure_kb_v04(kb)
    ensure_run_v04(out)
    nodes = read_jsonl(kb / "nodes.jsonl")
    if not nodes:
        raise SystemExit(f"No nodes in {kb / 'nodes.jsonl'}; run add first.")
    gaps = build_gaps_v04(nodes, focus, max_gaps=max_gaps)
    write_jsonl(out / "gaps.jsonl", gaps)
    # Merge into persistent KB gaps (idempotent by gap_id).
    kb_gaps = read_jsonl(kb / "gaps.jsonl")
    known = {g.get("gap_id") for g in kb_gaps}
    new_kb_gaps = [g for g in gaps if g["gap_id"] not in known]
    append_jsonl(kb / "gaps.jsonl", new_kb_gaps)
    write_loop_state(out, kb, "G3_MAP", ["G1_INPUT", "G2_FIELD", "G3_MAP"],
                     focus, {"nodes": len(nodes), "gaps": len(gaps)},
                     "generate hypotheses (hypothesis command)")
    print(f"[map] {len(gaps)} gaps -> {out / 'gaps.jsonl'} ({len(new_kb_gaps)} new in KB)")
    return gaps


# ---------------------------------------------------------------------------
# Gate 4a command
# ---------------------------------------------------------------------------


def run_hypothesis(kb: Path, out: Path, focus: str, max_per_gap: int) -> List[dict]:
    ensure_kb_v04(kb)
    ensure_run_v04(out)
    nodes = read_jsonl(kb / "nodes.jsonl")
    nodes_by_id = {n["node_id"]: n for n in nodes if "node_id" in n}
    gaps = read_jsonl(out / "gaps.jsonl") or read_jsonl(kb / "gaps.jsonl")
    if not gaps:
        raise SystemExit("No gaps found; run map first.")
    # If focus was passed and gaps lack it, keep as-is (gaps already carry focus).

    hyps: List[dict] = []
    for gap in gaps:
        hyps.extend(hypotheses_from_gap(gap, nodes_by_id, focus, max_per_gap))
    # back-fill related_hypotheses on the run gaps
    by_gap: Dict[str, List[str]] = {}
    for h in hyps:
        by_gap.setdefault(h["gap_id"], []).append(h["hypothesis_id"])
    for gap in gaps:
        gap["related_hypotheses"] = by_gap.get(gap["gap_id"], [])
    write_jsonl(out / "gaps.jsonl", gaps)

    write_jsonl(out / "hypothesis_candidates.jsonl", hyps)

    lineage = _lineage_rows(hyps, {g["gap_id"]: g for g in gaps}, nodes_by_id)
    mother_patches = _mother_patch_rows(hyps, {g["gap_id"]: g for g in gaps})
    review_states = _review_state_rows(hyps)
    write_jsonl(out / "lineage.jsonl", lineage)
    write_jsonl(out / "mother_patch.jsonl", mother_patches)
    write_jsonl(out / "review_state.jsonl", review_states)

    # Persist into KB stores (idempotent/upsert by stable ids).
    kb_hyps = read_jsonl(kb / "hypotheses.jsonl")
    known = {h.get("hypothesis_id") for h in kb_hyps}
    new_kb = [h for h in hyps if h["hypothesis_id"] not in known]
    append_jsonl(kb / "hypotheses.jsonl", new_kb)
    _upsert_jsonl(kb / "lineage.jsonl", lineage, "lineage_id")
    _upsert_jsonl(kb / "mother_patch.jsonl", mother_patches, "mother_patch_id")
    _upsert_jsonl(kb / "review_state.jsonl", review_states, "review_state_id")

    counts = {"nodes": len(nodes), "gaps": len(gaps), "hypotheses": len(hyps),
              "lineage": len(lineage), "mother_patches": len(mother_patches),
              "review_states": len(review_states)}
    write_loop_state(out, kb, "G4_GENERATE",
                     ["G1_INPUT", "G2_FIELD", "G3_MAP", "G4_GENERATE"],
                     focus, counts, "validate hypotheses (validate command)")
    print(f"[hypothesis] {len(hyps)} candidates -> {out / 'hypothesis_candidates.jsonl'} ({len(new_kb)} new in KB)")
    return hyps


# ---------------------------------------------------------------------------
# Gate 4b command
# ---------------------------------------------------------------------------


def _validation_notes_md(validations: List[dict], hyps_by_id: Dict[str, dict]) -> str:
    summary_counts: Dict[str, int] = {}
    status_counts: Dict[str, int] = {}
    for v in validations:
        summary_counts[v["six_eyes_summary"]] = summary_counts.get(v["six_eyes_summary"], 0) + 1
        status_counts[v["status"]] = status_counts.get(v["status"], 0) + 1
    lines = [
        "# Validation Notes (Six-Eyes)",
        "",
        f"- generated_at: {utc_now()}",
        f"- total validations: {len(validations)}",
        f"- six_eyes_summary: {json.dumps(summary_counts, ensure_ascii=False, sort_keys=True)}",
        f"- validation_status: {json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "> Six-Eyes = 证眼(evidence) 源眼(provenance) 构眼(contradiction) "
        "隙眼(falsifiability) 生眼(novelty) 界眼(boundary). "
        "It lives inside the Generate+Validate gate, not as a separate gate.",
        "",
        "## Per-hypothesis verdicts",
        "",
    ]
    for v in validations:
        h = hyps_by_id.get(v["hypothesis_id"], {})
        lines.append(f"### `{v['hypothesis_id']}` — {v['six_eyes_summary'].upper()} (status: {v['status']})")
        lines.append("")
        lines.append(f"- gap: `{v.get('gap_id')}`")
        lines.append(f"- claim: {h.get('claim','(claim not found)')}")
        lines.append(f"- evidence_level: {v['evidence_level']}")
        for eye_name, eye in v["six_eyes"].items():
            lines.append(f"  - {eye_name}: **{eye['status']}** — {eye['notes']}")
        lines.append(f"- next_test: {v['next_test']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_validate(kb: Path, out: Path) -> List[dict]:
    ensure_kb_v04(kb)
    ensure_run_v04(out)
    hyps = read_jsonl(out / "hypothesis_candidates.jsonl")
    if not hyps:
        raise SystemExit("No hypothesis_candidates.jsonl; run hypothesis first.")
    gaps = read_jsonl(out / "gaps.jsonl") or read_jsonl(kb / "gaps.jsonl")
    nodes = read_jsonl(kb / "nodes.jsonl")
    gaps_by_id = {g["gap_id"]: g for g in gaps}
    nodes_by_id = {n["node_id"]: n for n in nodes if "node_id" in n}
    anti_patterns = read_jsonl(kb / "anti_patterns.jsonl")

    validations = validate_hypotheses(hyps, gaps_by_id, nodes_by_id, anti_patterns)
    write_jsonl(out / "validations.jsonl", validations)

    # Update hypothesis validation_status in the run file + KB.
    validations_by_hyp = {v["hypothesis_id"]: v for v in validations}
    status_by_hyp = {v["hypothesis_id"]: v["status"] for v in validations}
    for h in hyps:
        h["validation_status"] = status_by_hyp.get(h["hypothesis_id"], h.get("validation_status", "pending"))
    write_jsonl(out / "hypothesis_candidates.jsonl", hyps)

    lineage = _lineage_rows(hyps, gaps_by_id, nodes_by_id)
    mother_patches = _mother_patch_rows(hyps, gaps_by_id, validations_by_hyp)
    review_states = _review_state_rows(hyps, validations_by_hyp)
    write_jsonl(out / "lineage.jsonl", lineage)
    write_jsonl(out / "mother_patch.jsonl", mother_patches)
    write_jsonl(out / "review_state.jsonl", review_states)

    hyps_by_id = {h["hypothesis_id"]: h for h in hyps}
    (out / "validation_notes.md").write_text(_validation_notes_md(validations, hyps_by_id), encoding="utf-8")

    # Persist validations and spine stores into KB (idempotent/upsert by stable ids).
    kb_vals = read_jsonl(kb / "validations.jsonl")
    known = {v.get("validation_id") for v in kb_vals}
    append_jsonl(kb / "validations.jsonl", [v for v in validations if v["validation_id"] not in known])
    _upsert_jsonl(kb / "hypotheses.jsonl", hyps, "hypothesis_id")
    _upsert_jsonl(kb / "lineage.jsonl", lineage, "lineage_id")
    _upsert_jsonl(kb / "mother_patch.jsonl", mother_patches, "mother_patch_id")
    _upsert_jsonl(kb / "review_state.jsonl", review_states, "review_state_id")

    counts = {"nodes": len(nodes), "gaps": len(gaps),
              "hypotheses": len(hyps), "validations": len(validations),
              "lineage": len(lineage), "mother_patches": len(mother_patches),
              "review_states": len(review_states)}
    write_loop_state(out, kb, "G4_VALIDATE",
                     ["G1_INPUT", "G2_FIELD", "G3_MAP", "G4_GENERATE", "G4_VALIDATE"],
                     read_loop_state(out).get("focus", "") if read_loop_state(out) else "",
                     counts, "crystallize return contract (return command)")
    print(f"[validate] {len(validations)} validations -> {out / 'validations.jsonl'}")
    return validations


# ---------------------------------------------------------------------------
# Gate 5 command: return + consolidate
# ---------------------------------------------------------------------------


def _artifact_cards(out: Path, kb: Path, counts: Dict[str, int]) -> List[dict]:
    return [
        {"artifact_id": "card_gaps", "type": "gap_map", "title": "Gap map",
         "path": str(out / "gaps.jsonl"), "count": counts.get("gaps", 0),
         "reuse_level": "project", "evidence_level": "candidate"},
        {"artifact_id": "card_hypotheses", "type": "hypothesis_candidates",
         "title": "Hypothesis candidates", "path": str(out / "hypothesis_candidates.jsonl"),
         "count": counts.get("hypotheses", 0), "reuse_level": "project", "evidence_level": "speculative"},
        {"artifact_id": "card_validations", "type": "validations",
         "title": "Six-Eyes validations", "path": str(out / "validations.jsonl"),
         "count": counts.get("validations", 0), "reuse_level": "project", "evidence_level": "traceable"},
        {"artifact_id": "card_lineage", "type": "lineage",
         "title": "Hypothesis lineage", "path": str(out / "lineage.jsonl"),
         "count": counts.get("lineage", 0), "reuse_level": "project", "evidence_level": "traceable"},
        {"artifact_id": "card_mother_patch", "type": "mother_patch",
         "title": "Mother patches", "path": str(out / "mother_patch.jsonl"),
         "count": counts.get("mother_patches", 0), "reuse_level": "project", "evidence_level": "candidate"},
        {"artifact_id": "card_review_state", "type": "review_state",
         "title": "Review states", "path": str(out / "review_state.jsonl"),
         "count": counts.get("review_states", 0), "reuse_level": "project", "evidence_level": "traceable"},
        {"artifact_id": "card_return", "type": "return_contract",
         "title": "Return contract", "path": str(out / "return_contract.md"),
         "count": 1, "reuse_level": "portable", "evidence_level": "traceable"},
    ]


def _return_contract_md(out: Path, kb: Path, focus: str, counts: Dict[str, int],
                        validations: List[dict]) -> str:
    summary_counts: Dict[str, int] = {}
    for v in validations:
        summary_counts[v["six_eyes_summary"]] = summary_counts.get(v["six_eyes_summary"], 0) + 1
    lines = [
        "# Return Contract (V0.4)",
        "",
        f"- loop_id: `{out.name}`",
        f"- loop_version: v0.4",
        f"- focus: {focus}",
        f"- kb: `{kb}`",
        f"- updated_at: {utc_now()}",
        "",
        "## Gates completed",
        "1. Input gate — distilled sources ingested.",
        "2. Knowledge field gate — persistent KB updated.",
        "3. Map gate — gap operators applied.",
        "4. Generate+Validate gate — hypotheses + Six-Eyes validation.",
        "5. Return gate — this contract + consolidation.",
        "",
        "## Counts",
        f"- sources/nodes/gaps/hypotheses/validations: "
        f"{counts.get('sources','?')}/{counts.get('nodes',0)}/{counts.get('gaps',0)}/"
        f"{counts.get('hypotheses',0)}/{counts.get('validations',0)}",
        f"- six_eyes_summary: {json.dumps(summary_counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Key artifacts",
        f"- gaps: `{out / 'gaps.jsonl'}`",
        f"- hypotheses: `{out / 'hypothesis_candidates.jsonl'}`",
        f"- validations: `{out / 'validations.jsonl'}`",
        f"- lineage: `{out / 'lineage.jsonl'}`",
        f"- mother_patch: `{out / 'mother_patch.jsonl'}`",
        f"- review_state: `{out / 'review_state.jsonl'}`",
        f"- validation notes: `{out / 'validation_notes.md'}`",
        f"- final report: `{out / 'final_report.md'}`",
        "",
        "## Evidence boundary",
        "- All hypotheses are *candidates*, not verified scientific facts.",
        "- Six-Eyes is a stdlib heuristic, not a full LLM review; `reject`/`needs_review` must be human-checked.",
        "- `mother_patch.jsonl` is a proposed patch queue, not an automatic world-model rewrite.",
        "- Distilled summaries are not full-text verification; confirm against primary literature.",
        "",
        "## Next loop entry",
        "1. Take `supported` hypotheses to a simulation/experiment/observation gate.",
        "2. Record any rejected reconciliation as an anti-pattern (already consolidated).",
        "3. Re-run map after adding new distilled sources.",
        "",
        "## Do not repeat",
        "- Do not re-ingest a source whose content hash is already in `sources.jsonl`.",
        "- Do not promote a candidate hypothesis to a verified conclusion.",
    ]
    return "\n".join(lines) + "\n"


def _final_report_md(out: Path, kb: Path, focus: str, counts: Dict[str, int],
                     gaps: List[dict], hyps: List[dict], validations: List[dict]) -> str:
    val_by_hyp = {v["hypothesis_id"]: v for v in validations}
    gaps_by_id = {g["gap_id"]: g for g in gaps}
    # rank hypotheses: supported > weakened > needs_data > rejected, then confidence
    rank = {"supported": 0, "weakened": 1, "needs_data": 2, "pending": 3, "rejected": 4}
    ranked = sorted(hyps, key=lambda h: (rank.get(h.get("validation_status"), 5),
                                         -h.get("confidence", 0)))
    lines = [
        f"# Final Report — {focus or out.name}",
        "",
        f"- loop_id: `{out.name}` (v0.4)",
        f"- generated_at: {utc_now()}",
        f"- focus: {focus}",
        "",
        "> ⚠️ All claims below are **candidate hypotheses** generated by stdlib heuristics "
        "from distilled paper summaries. They are not verified scientific facts.",
        "",
        "## Summary counts",
        f"- sources: {counts.get('sources','?')}",
        f"- nodes: {counts.get('nodes',0)}",
        f"- gaps: {counts.get('gaps',0)}",
        f"- hypotheses: {counts.get('hypotheses',0)}",
        f"- validations: {counts.get('validations',0)}",
        "",
        "## Gap types found",
    ]
    gt_counts: Dict[str, int] = {}
    for g in gaps:
        gt_counts[g["gap_type"]] = gt_counts.get(g["gap_type"], 0) + 1
    for gt, c in sorted(gt_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {gt}: {c}")
    lines += ["", "## Ranked candidate hypotheses", ""]
    for h in ranked:
        v = val_by_hyp.get(h["hypothesis_id"], {})
        g = gaps_by_id.get(h.get("gap_id"), {})
        lines.append(f"### `{h['hypothesis_id']}` — {h.get('validation_status','pending')} "
                     f"(six-eyes: {v.get('six_eyes_summary','?')})")
        lines.append("")
        lines.append(f"- gap: `{h.get('gap_id')}` ({g.get('gap_type','?')}, {g.get('operator','?')})")
        lines.append(f"- claim: {h.get('claim')}")
        lines.append(f"- minimal explanation: {h.get('minimal_explanation')}")
        lines.append(f"- reasoning_type: {h.get('reasoning_type')}; confidence: {h.get('confidence')}")
        lines.append(f"- source_nodes: {', '.join(h.get('source_nodes', [])) or '(none)'}")
        lines.append(f"- lineage_id: `lin_{stable_hash(h['hypothesis_id'])[:12]}`")
        lines.append(f"- mother_patch_id: `patch_{stable_hash(h['hypothesis_id'])[:12]}`")
        lines.append(f"- review_state_id: `review_{stable_hash(h['hypothesis_id'])[:12]}`")
        lines.append(f"- proposed_validation: {', '.join(h.get('proposed_validation', []))}")
        if v.get("next_test"):
            lines.append(f"- next_test: {v['next_test']}")
        lines.append("")
    lines += [
        "## Limitations",
        "- Heuristic gap operators and Six-Eyes; no LLM, no web, no full-text verification.",
        "- Conservative language is intentional: these are seeds for further testing.",
        "",
    ]
    return "\n".join(lines) + "\n"


def consolidate_world_model(kb: Path) -> Dict[str, int]:
    """Light consolidation: roll up KB counts into world_model.md and record
    rejected reconciliations as anti-patterns (idempotent)."""
    sources = read_jsonl(kb / "sources.jsonl")
    nodes = read_jsonl(kb / "nodes.jsonl")
    gaps = read_jsonl(kb / "gaps.jsonl")
    hyps = read_jsonl(kb / "hypotheses.jsonl")
    vals = read_jsonl(kb / "validations.jsonl")
    lineage = read_jsonl(kb / "lineage.jsonl")
    mother_patches = read_jsonl(kb / "mother_patch.jsonl")
    review_states = read_jsonl(kb / "review_state.jsonl")

    # Anti-patterns: rejected hypotheses -> remember the claim so novelty_eye can fail it later.
    hyps_by_id = {h["hypothesis_id"]: h for h in hyps}
    existing_ap = read_jsonl(kb / "anti_patterns.jsonl")
    known_ap = {ap.get("anti_pattern_id") for ap in existing_ap}
    new_ap: List[dict] = []
    for v in vals:
        if v.get("six_eyes_summary") == "reject":
            h = hyps_by_id.get(v["hypothesis_id"], {})
            claim = h.get("claim", "")
            claim_norm = re.sub(r"\s+", " ", claim.lower()).strip()
            ap_id = "anti_" + stable_hash(claim_norm)[:12]
            if ap_id in known_ap or not claim_norm:
                continue
            known_ap.add(ap_id)
            new_ap.append({
                "anti_pattern_id": ap_id,
                "claim_norm": claim_norm,
                "origin_hypothesis": v["hypothesis_id"],
                "reason": "Six-Eyes summary = reject.",
                "six_eyes": v.get("six_eyes", {}),
                "recorded_at": utc_now(),
            })
    append_jsonl(kb / "anti_patterns.jsonl", new_ap)

    status_counts: Dict[str, int] = {}
    for v in vals:
        status_counts[v["six_eyes_summary"]] = status_counts.get(v["six_eyes_summary"], 0) + 1

    md = [
        "# World Model",
        "",
        f"_Last consolidated: {utc_now()}_",
        "",
        "Persistent knowledge field for the Shengwanwu Loop (V0.4). "
        "Everything here is accumulated across runs; hypotheses are candidates.",
        "",
        "## Field size",
        f"- sources: {len(sources)}",
        f"- nodes: {len(nodes)}",
        f"- gaps: {len(gaps)}",
        f"- hypotheses: {len(hyps)}",
        f"- validations: {len(vals)}",
        f"- lineage: {len(lineage)}",
        f"- mother_patches: {len(mother_patches)}",
        f"- review_states: {len(review_states)}",
        f"- anti_patterns: {len(existing_ap) + len(new_ap)}",
        "",
        "## Six-Eyes verdict distribution",
        f"- {json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Sources in field",
    ]
    for s in sources:
        md.append(f"- `{s.get('source_id')}` ({', '.join(s.get('unit_ids', []))}) "
                  f"— {s.get('node_count',0)} nodes — {s.get('path')}")
    md += [
        "",
        "## Gap-type coverage",
    ]
    gt_counts: Dict[str, int] = {}
    for g in gaps:
        gt_counts[g.get("gap_type", "?")] = gt_counts.get(g.get("gap_type", "?"), 0) + 1
    for gt, c in sorted(gt_counts.items(), key=lambda kv: -kv[1]):
        md.append(f"- {gt}: {c}")
    md += [
        "",
        "## Boundary",
        "- Candidate hypotheses only; verify against primary literature before any claim of fact.",
        "",
    ]
    (kb / "world_model.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    return {"new_anti_patterns": len(new_ap),
            "anti_patterns_total": len(existing_ap) + len(new_ap)}


def run_return(kb: Path, out: Path, write_report: bool, consolidate: bool) -> None:
    ensure_kb_v04(kb)
    ensure_run_v04(out)
    state = read_loop_state(out) or {}
    focus = state.get("focus", "")
    gaps = read_jsonl(out / "gaps.jsonl")
    hyps = read_jsonl(out / "hypothesis_candidates.jsonl")
    validations = read_jsonl(out / "validations.jsonl")
    lineage = read_jsonl(out / "lineage.jsonl")
    mother_patches = read_jsonl(out / "mother_patch.jsonl")
    review_states = read_jsonl(out / "review_state.jsonl")
    if not validations:
        raise SystemExit("No validations.jsonl; run validate first.")

    sources = read_jsonl(kb / "sources.jsonl")
    nodes = read_jsonl(kb / "nodes.jsonl")
    counts = {
        "sources": len(sources),
        "nodes": len(nodes),
        "gaps": len(gaps),
        "hypotheses": len(hyps),
        "validations": len(validations),
        "lineage": len(lineage),
        "mother_patches": len(mother_patches),
        "review_states": len(review_states),
    }

    write_jsonl(out / "artifact_cards.jsonl", _artifact_cards(out, kb, counts))
    (out / "return_contract.md").write_text(
        _return_contract_md(out, kb, focus, counts, validations), encoding="utf-8")

    if write_report:
        (out / "final_report.md").write_text(
            _final_report_md(out, kb, focus, counts, gaps, hyps, validations), encoding="utf-8")
    else:
        # Always ensure the required artifact exists.
        if not (out / "final_report.md").exists():
            (out / "final_report.md").write_text(
                _final_report_md(out, kb, focus, counts, gaps, hyps, validations), encoding="utf-8")

    consolidate_info = {}
    if consolidate:
        consolidate_info = consolidate_world_model(kb)

    write_loop_state(out, kb, "G5_RETURN", list(V04_STATES),
                     focus, counts, "next loop: take supported hypotheses to a test gate")
    print(f"[return] return_contract.md + final_report.md + artifact_cards.jsonl -> {out}")
    if consolidate:
        print(f"[consolidate] world_model.md updated; "
              f"+{consolidate_info.get('new_anti_patterns',0)} anti-patterns "
              f"(total {consolidate_info.get('anti_patterns_total',0)})")
