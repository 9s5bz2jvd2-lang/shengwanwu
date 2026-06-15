#!/usr/bin/env python3
"""
hypothesis_scan_v02.py — World Model Hypothesis Scanner v0.2
============================================================

Extends the v0.1 transitive-link / hub-co-occurrence scanner with four
new detection modes:

  v0.2a  Shared-Keywords-Without-Connection
  v0.2b  Concept-Level Contradiction Detection
  v0.2c  Method Gap Detection
  v0.2d  Cross-Domain Bridge Detection

Design rules
------------
* **Paper relationships** (edges, "are these two papers connected?") operate
  at the *unit_id* level — exactly as in v0.1.
* **Concept analysis** (what do the papers actually *say*?) operates at the
  *node_id* level, using each node's ``tags`` list and ``content`` text.

Input files (inside a library directory)
----------------------------------------
  source_catalog.jsonl   — paper metadata, including ``role``
  node_store.jsonl       — concept nodes with ``tags``
  graph_edges.jsonl      — unit_id-level edges

Output
------
Hypotheses are written to **stdout** (pretty-printed JSON array) and to
``hypothesis_results_v02.json`` inside the library directory.

Usage
-----
    python hypothesis_scan_v02.py <library_dir>
"""

import json
import sys
import os
import re
import itertools
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════
#  Constants — domain knowledge for text-level analysis
# ═══════════════════════════════════════════════════════════════

# Pairs of competing approaches.  Each entry is
#   (pole_a_indicators, pole_b_indicators, human_description)
# A node "belongs" to a pole if any indicator appears in its tags **or**
# its lower-cased content words.
CONTRADICTION_AXES = [
    (
        {"diffusion", "score-based"},
        {"autoregressive", "token", "gpt", "next-token", "discrete"},
        "Diffusion-based generation vs. autoregressive/token-based generation",
    ),
    (
        {"data_driven", "approximation", "implicit_physics", "implicit"},
        {"physics_informed", "physics", "pinn", "physics_constraint"},
        "Data-driven/implicit learning vs. explicit physics-informed modeling",
    ),
    (
        {"stochastic", "multi_modal", "uncertainty"},
        {"deterministic", "recurrent"},
        "Stochastic multi-modal futures vs. deterministic recurrent dynamics",
    ),
    (
        {"continuous"},
        {"discrete"},
        "Continuous vs. discrete latent state representation",
    ),
]

# Regex cues for evaluative language in node content.
_SUPERIORITY_RE = re.compile(
    r"\b(better|outperform|surpass|exceed|prefer|superior|"
    r"more accurate|higher fidelity|stronger|challenge|prevail|"
    r"first|novel|viable alternative)\b",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"\b(fail|limit|limitation|cannot|unable|struggle|difficult|"
    r"expensive|costly|barrier|lose|loss|degrad|trade.?off|"
    r"but|however|whereas|while|gap)\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════
#  Data loading & indexing
# ═══════════════════════════════════════════════════════════════

def load_jsonl(path):
    """Read a JSON-Lines file into a list of dicts (empty list if absent)."""
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_library(lib_dir):
    """Load the three core data files from *lib_dir*."""
    catalog = load_jsonl(os.path.join(lib_dir, "source_catalog.jsonl"))
    nodes = load_jsonl(os.path.join(lib_dir, "node_store.jsonl"))
    edges = load_jsonl(os.path.join(lib_dir, "graph_edges.jsonl"))
    return catalog, nodes, edges


class Index:
    """Pre-computed indices shared by all detectors."""

    def __init__(self, catalog, nodes, edges):
        self.nodes = nodes
        self.edges = edges
        self.catalog = catalog

        # unit_id lookups
        self.unit_ids = sorted({n["unit_id"] for n in nodes})
        self.unit_title = {
            c["unit_id"]: c.get("title", c["unit_id"]) for c in catalog
        }
        self.unit_role = {
            c["unit_id"]: c.get("role", "unknown") for c in catalog
        }

        # unit_id -> set of all tags aggregated across its nodes
        self.unit_tags = defaultdict(set)
        for n in nodes:
            for t in n.get("tags", []):
                self.unit_tags[n["unit_id"]].add(t)

        # unit_id -> list of node dicts
        self.unit_nodes = defaultdict(list)
        for n in nodes:
            self.unit_nodes[n["unit_id"]].append(n)

        # node_id -> node dict
        self.node_by_id = {n["node_id"]: n for n in nodes}

        # tag -> list of nodes carrying that tag
        self.tag_to_nodes = defaultdict(list)
        for n in nodes:
            for t in n.get("tags", []):
                self.tag_to_nodes[t].append(n)

        # tag -> set of unit_ids that have the tag (for rarity calc)
        self.tag_to_units = defaultdict(set)
        for uid, tags in self.unit_tags.items():
            for t in tags:
                self.tag_to_units[t].add(uid)

        # Edge adjacency (treated as undirected for "is there a link?")
        self.out_adj = defaultdict(set)
        self.in_adj = defaultdict(set)
        self.edge_pairs = set()  # frozenset({src, tgt})
        for e in edges:
            s, t = e["source"], e["target"]
            self.out_adj[s].add(t)
            self.in_adj[t].add(s)
            self.edge_pairs.add(frozenset({s, t}))

        # node_id -> set of lower-case words in content  (+ tags)
        self._word_cache = {}

    # ── helpers ───────────────────────────────────────────────

    def has_edge(self, u1, u2):
        """True if a direct edge exists between *u1* and *u2* (either dir)."""
        return frozenset({u1, u2}) in self.edge_pairs

    def node_words(self, node):
        """Lower-cased word-set of a node's content + tags (cached)."""
        nid = node["node_id"]
        if nid not in self._word_cache:
            text = node.get("content", "").lower()
            words = set(re.findall(r"[a-z_]+", text))
            for t in node.get("tags", []):
                words.add(t.lower())
            self._word_cache[nid] = words
        return self._word_cache[nid]

    def tag_rarity(self, tag):
        """How many distinct unit_ids carry *tag* (fewer = rarer)."""
        return len(self.tag_to_units.get(tag, set()))


# ═══════════════════════════════════════════════════════════════
#  v0.1 detectors (retained, reformatted to v0.2 schema)
# ═══════════════════════════════════════════════════════════════

def detect_transitive_links(idx):
    """v0.1 mode — A→B→C transitive missing link (unit_id level)."""
    results = []
    seen = set()
    for a in idx.unit_ids:
        for b in idx.out_adj.get(a, set()):
            for c in idx.out_adj.get(b, set()):
                if c == a or idx.has_edge(a, c):
                    continue
                key = (a, c)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "type": "transitive_missing_link",
                    "description": (
                        f"Potential missing link {a} → {c} "
                        f"(transitive path {a}→{b}→{c})"
                    ),
                    "source_papers": [a, c],
                    "evidence": [{
                        "path": [a, b, c],
                        "rationale": f"{a} links to {b}, {b} links to {c}, "
                                     f"but {a} and {c} are not directly linked",
                    }],
                    "score": 0.5,
                    "novelty_rating": "medium",
                })
    return results


def detect_hub_cooccurrence(idx):
    """v0.1 mode — two papers share ≥2 neighbors but no direct edge."""
    results = []
    uids = idx.unit_ids
    for i, a in enumerate(uids):
        for b in uids[i + 1:]:
            if idx.has_edge(a, b):
                continue
            neigh_a = idx.out_adj.get(a, set()) | idx.in_adj.get(a, set())
            neigh_b = idx.out_adj.get(b, set()) | idx.in_adj.get(b, set())
            shared = neigh_a & neigh_b
            if len(shared) >= 2:
                results.append({
                    "type": "hub_cooccurrence",
                    "description": (
                        f"Papers {a} and {b} share {len(shared)} graph "
                        f"neighbors but have no direct edge"
                    ),
                    "source_papers": [a, b],
                    "evidence": [{
                        "shared_neighbors": sorted(shared),
                        "rationale": "Hub co-occurrence suggests latent correlation",
                    }],
                    "score": round(min(0.4 + 0.1 * len(shared), 0.9), 2),
                    "novelty_rating": "medium",
                })
    return results


# ═══════════════════════════════════════════════════════════════
#  v0.2a — Shared Keywords Without Connection
# ═══════════════════════════════════════════════════════════════

def detect_shared_keywords_no_connection(idx):
    """
    Find pairs of papers (unit_ids) that share common tags/keywords but
    have **no** direct edge.  Generates a hypothesis about a latent link.
    """
    results = []
    uids = idx.unit_ids
    for i, a in enumerate(uids):
        for b in uids[i + 1:]:
            if idx.has_edge(a, b):
                continue
            shared = sorted(idx.unit_tags[a] & idx.unit_tags[b])
            if not shared:
                continue

            # Score: base + bonus per shared keyword (capped)
            score = round(min(0.35 + 0.12 * len(shared), 0.92), 2)

            # Novelty: higher when shared tags are rare
            min_rarity = min(idx.tag_rarity(t) for t in shared)
            novelty = "high" if min_rarity <= 2 else (
                "medium" if min_rarity <= 4 else "low"
            )

            title_a = idx.unit_title.get(a, a)
            title_b = idx.unit_title.get(b, b)

            results.append({
                "type": "shared_keywords_no_connection",
                "description": (
                    f'"{title_a}" ({a}) and "{title_b}" ({b}) share '
                    f'keywords {shared} but are not directly linked. '
                    f'They may share a deeper conceptual connection worth '
                    f'investigating.'
                ),
                "source_papers": [a, b],
                "evidence": [{
                    "shared_keywords": shared,
                    "paper_a_tags": sorted(idx.unit_tags[a]),
                    "paper_b_tags": sorted(idx.unit_tags[b]),
                    "edge_exists": False,
                }],
                "score": score,
                "novelty_rating": novelty,
            })
    return results


# ═══════════════════════════════════════════════════════════════
#  v0.2b — Concept-Level Contradiction Detection
# ═══════════════════════════════════════════════════════════════

def _check_opposing(node_a, node_b, idx):
    """
    Return ``(axis_description, detail)`` if *node_a* and *node_b* sit on
    opposite poles of any CONTRADICTION_AXIS, else ``None``.
    """
    words_a = idx.node_words(node_a)
    words_b = idx.node_words(node_b)
    for pole_a, pole_b, desc in CONTRADICTION_AXES:
        a_hits_a = pole_a & words_a
        b_hits_b = pole_b & words_b
        a_hits_b = pole_a & words_b
        b_hits_a = pole_b & words_a
        if (a_hits_a and b_hits_b):
            return desc, {
                "pole_a_node": node_a["node_id"],
                "pole_a_indicators": sorted(a_hits_a),
                "pole_b_node": node_b["node_id"],
                "pole_b_indicators": sorted(b_hits_b),
            }
        if (b_hits_a and a_hits_b):
            return desc, {
                "pole_a_node": node_b["node_id"],
                "pole_a_indicators": sorted(a_hits_b),
                "pole_b_node": node_a["node_id"],
                "pole_b_indicators": sorted(b_hits_a),
            }
    return None


def detect_contradictions(idx):
    """
    Find nodes (from different unit_ids) with overlapping tags whose content
    describes contradictory or competing approaches.
    """
    results = []
    seen_pairs = set()

    for n_a, n_b in itertools.combinations(idx.nodes, 2):
        if n_a["unit_id"] == n_b["unit_id"]:
            continue

        # Must share at least one tag (or very similar tag)
        tags_a = set(n_a.get("tags", []))
        tags_b = set(n_b.get("tags", []))
        shared = tags_a & tags_b
        if not shared:
            # Allow "similar" tags: same alphanumeric root
            roots_a = {re.sub(r"[^a-z]", "", t.lower()) for t in tags_a}
            roots_b = {re.sub(r"[^a-z]", "", t.lower()) for t in tags_b}
            if not (roots_a & roots_b):
                continue
            shared = {"(similar tags)"}

        pair_key = frozenset({n_a["node_id"], n_b["node_id"]})
        if pair_key in seen_pairs:
            continue

        opposing = _check_opposing(n_a, n_b, idx)
        if opposing is None:
            continue

        axis_desc, detail = opposing
        seen_pairs.add(pair_key)

        content_a = n_a.get("content", "")
        content_b = n_b.get("content", "")
        has_sup = bool(_SUPERIORITY_RE.search(content_a) or
                       _SUPERIORITY_RE.search(content_b))
        has_lim = bool(_LIMITATION_RE.search(content_a) or
                       _LIMITATION_RE.search(content_b))

        score = round(min(
            0.55 + 0.08 * len(shared) + 0.12 * has_sup + 0.08 * has_lim,
            0.95,
        ), 2)

        ua, ub = n_a["unit_id"], n_b["unit_id"]
        results.append({
            "type": "concept_contradiction",
            "description": (
                f"Competing approaches detected: {axis_desc}. "
                f'Node {n_a["node_id"]} ({ua}) and node {n_b["node_id"]} '
                f"({ub}) share tags {sorted(shared)} but advocate opposing "
                f"sides of this axis."
            ),
            "source_papers": [ua, ub],
            "evidence": {
                "axis": axis_desc,
                "shared_tags": sorted(shared),
                "node_a": {
                    "node_id": n_a["node_id"],
                    "unit_id": ua,
                    "content": content_a,
                },
                "node_b": {
                    "node_id": n_b["node_id"],
                    "unit_id": ub,
                    "content": content_b,
                },
                "pole_detail": detail,
                "superiority_cue_present": has_sup,
                "limitation_cue_present": has_lim,
            },
            "score": score,
            "novelty_rating": "high",
        })

    return results


# ═══════════════════════════════════════════════════════════════
#  v0.2c — Method Gap Detection
# ═══════════════════════════════════════════════════════════════

def detect_method_gaps(idx):
    """
    For each edge (A → B), compare the tag sets of A's nodes and B's nodes.
    Tags present in one but absent in the other represent a *method gap* —
    a concept that could be transferred.
    """
    results = []
    for e in idx.edges:
        src, tgt = e["source"], e["target"]
        tags_src = idx.unit_tags.get(src, set())
        tags_tgt = idx.unit_tags.get(tgt, set())

        src_only = sorted(tags_src - tags_tgt)
        tgt_only = sorted(tags_tgt - tags_src)

        if not src_only and not tgt_only:
            continue

        # Pick the "gap" direction with the most missing methods (or src→tgt
        # if tied) — we can report both if significant.
        for gap_src, gap_tgt, missing in [
            (src, tgt, src_only),
            (tgt, src, tgt_only),
        ]:
            if not missing:
                continue

            title_s = idx.unit_title.get(gap_src, gap_src)
            title_t = idx.unit_title.get(gap_tgt, gap_tgt)
            present_tags = sorted(idx.unit_tags.get(gap_src, set()))

            score = round(min(0.4 + 0.08 * len(missing), 0.88), 2)

            results.append({
                "type": "method_gap",
                "description": (
                    f'"{title_t}" ({gap_tgt}) is connected to '
                    f'"{title_s}" ({gap_src}) but lacks methods/concepts '
                    f"present in the latter: {missing}. "
                    f"Transferring these could improve {gap_tgt}'s "
                    f"capabilities in those areas."
                ),
                "source_papers": [gap_src, gap_tgt],
                "evidence": {
                    "edge": {"source": src, "target": tgt, "type": e.get("type")},
                    "source_has": missing,
                    "target_missing": missing,
                    "source_all_tags": present_tags,
                    "target_all_tags": sorted(idx.unit_tags.get(gap_tgt, set())),
                },
                "score": score,
                "novelty_rating": "medium",
            })
    return results


# ═══════════════════════════════════════════════════════════════
#  v0.2d — Cross-Domain Bridge Detection
# ═══════════════════════════════════════════════════════════════

def detect_cross_domain_bridges(idx):
    """
    Find nodes from different unit_ids that share tags but whose papers
    belong to different *role* categories (e.g. shared_core vs
    routed_expert).  This signals a cross-domain concept-transfer
    opportunity.
    """
    results = []
    seen = set()

    # Iterate over tags that appear in ≥2 unit_ids
    for tag, node_list in idx.tag_to_nodes.items():
        units_with_tag = idx.tag_to_units.get(tag, set())
        if len(units_with_tag) < 2:
            continue

        for n_a, n_b in itertools.combinations(node_list, 2):
            ua, ub = n_a["unit_id"], n_b["unit_id"]
            if ua == ub:
                continue
            role_a = idx.unit_role.get(ua, "unknown")
            role_b = idx.unit_role.get(ub, "unknown")
            if role_a == role_b:
                continue  # same role — not a cross-domain bridge

            key = frozenset({n_a["node_id"], n_b["node_id"]})
            if key in seen:
                continue
            seen.add(key)

            score = round(min(0.5 + 0.1 * len(units_with_tag), 0.9), 2)

            results.append({
                "type": "cross_domain_bridge",
                "description": (
                    f"Cross-domain bridge: nodes {n_a['node_id']} ({ua}, "
                    f"role={role_a}) and {n_b['node_id']} ({ub}, "
                    f"role={role_b}) share tag '{tag}' despite belonging to "
                    f"different role categories. This indicates a concept "
                    f"that could transfer across domains."
                ),
                "source_papers": [ua, ub],
                "evidence": {
                    "shared_tag": tag,
                    "role_a": role_a,
                    "role_b": role_b,
                    "node_a": {
                        "node_id": n_a["node_id"],
                        "content": n_a.get("content", ""),
                    },
                    "node_b": {
                        "node_id": n_b["node_id"],
                        "content": n_b.get("content", ""),
                    },
                },
                "score": score,
                "novelty_rating": "high",
            })
    return results


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

# Type → ID prefix mapping for stable, readable hypothesis IDs.
_TYPE_PREFIX = {
    "transitive_missing_link":       "TRANS",
    "hub_cooccurrence":              "HUB",
    "shared_keywords_no_connection": "SHARED",
    "concept_contradiction":         "CONTRA",
    "method_gap":                    "GAP",
    "cross_domain_bridge":           "BRIDGE",
}


def scan_hypotheses(idx):
    """
    Run all detectors and return a list of hypothesis dicts with IDs
    assigned.
    """
    detectors = [
        detect_transitive_links,
        detect_hub_cooccurrence,
        detect_shared_keywords_no_connection,
        detect_contradictions,
        detect_method_gaps,
        detect_cross_domain_bridges,
    ]

    all_hypotheses = []
    counters = defaultdict(int)

    for det in detectors:
        for hyp in det(idx):
            pfx = _TYPE_PREFIX.get(hyp["type"], "HYP")
            counters[pfx] += 1
            hyp["hypothesis_id"] = f"{pfx}-{counters[pfx]:03d}"
            all_hypotheses.append(hyp)

    # Sort by score descending for readability
    all_hypotheses.sort(key=lambda h: h["score"], reverse=True)
    return all_hypotheses


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python hypothesis_scan_v02.py <library_dir>")
        sys.exit(1)

    lib_dir = sys.argv[1]
    catalog, nodes, edges = load_library(lib_dir)

    if not nodes:
        print(f"Error: no nodes found in {lib_dir}/node_store.jsonl")
        sys.exit(1)

    idx = Index(catalog, nodes, edges)
    hypotheses = scan_hypotheses(idx)

    # Summary by type
    by_type = defaultdict(int)
    for h in hypotheses:
        by_type[h["type"]] += 1

    print("=" * 70)
    print("  Hypothesis Scan v0.2 — Results Summary")
    print("=" * 70)
    print(f"  Library:      {lib_dir}")
    print(f"  Papers:       {len(idx.unit_ids)}")
    print(f"  Nodes:        {len(nodes)}")
    print(f"  Edges:        {len(edges)}")
    print(f"  Hypotheses:   {len(hypotheses)}")
    print("-" * 70)
    for t, c in sorted(by_type.items()):
        print(f"    {t:40s}  {c}")
    print("=" * 70)
    print()

    # Full JSON output to stdout
    print(json.dumps(hypotheses, indent=2, ensure_ascii=False))

    # Write to file
    out_path = os.path.join(lib_dir, "hypothesis_results_v02.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(hypotheses, fh, indent=2, ensure_ascii=False)
    print(f"\n[Results written to {out_path}]", file=sys.stderr)

    return hypotheses


if __name__ == "__main__":
    main()
