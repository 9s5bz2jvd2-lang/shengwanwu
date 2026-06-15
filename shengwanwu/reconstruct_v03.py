#!/usr/bin/env python3
"""
hypothesis_reconstruct_v03.py — Semantic Hypothesis Reconstructor v0.3
=====================================================================

Three-layer architecture:
  Layer 1 (v0.2, existing): Graph pattern detection → raw hypotheses
  Layer 2 (NEW): Semantic context assembly → rich context packages
  Layer 3 (NEW): LLM-ready prompt generation → structured research questions

This script implements Layers 2 & 3:
  1. Loads v0.2 hypothesis results + node_store + graph_edges
  2. For each hypothesis: assembles a rich context package
  3. Semantic clustering: groups similar hypotheses (tag overlap + shared papers)
  4. Generates type-specific LLM prompts for each cluster
  5. Outputs prompt packages as JSONL for parallel daemon processing

Usage:
  python hypothesis_reconstruct_v03.py <library_dir> [--cluster] [--output prompts.jsonl]

After generating prompts, dispatch daemons to process them:
  Each daemon reads a batch of prompts and calls LLM to reconstruct research questions.
"""

import json
import sys
import os
import re
import math
from collections import defaultdict, Counter
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════
#  Layer 2: Semantic Context Assembly
# ═══════════════════════════════════════════════════════════════════

class ContextAssembler:
    """Assembles rich context packages from raw v0.2 hypotheses."""

    def __init__(self, nodes, edges, catalog=None):
        self.node_map = {n['node_id']: n for n in nodes}
        self.nodes_by_unit = defaultdict(list)
        for n in nodes:
            self.nodes_by_unit[n['unit_id']].append(n)
        self.edges = edges
        self.edge_index = defaultdict(list)
        for e in edges:
            self.edge_index[e.get('source_unit')].append(e)
            self.edge_index[e.get('target_unit')].append(e)
        self.catalog = catalog or {}

    def get_node_content(self, node_id):
        """Get full distilled content for a node."""
        node = self.node_map.get(node_id)
        if not node:
            return f"[Node {node_id} not found]"
        return node.get('content', '[No content]')

    def get_paper_nodes(self, unit_id, limit=5):
        """Get key nodes from a paper for additional context."""
        nodes = self.nodes_by_unit.get(unit_id, [])
        # Prioritize: method > finding > concept > result > limitation > others
        priority = {'method': 0, 'finding': 1, 'concept': 2, 'result': 3, 'limitation': 4}
        sorted_nodes = sorted(nodes, key=lambda n: priority.get(n.get('node_type', ''), 9))
        return [{'node_id': n['node_id'], 'node_type': n['node_type'],
                 'content': n['content'][:500], 'tags': n.get('tags', [])}
                for n in sorted_nodes[:limit]]

    def get_related_edges(self, unit_ids):
        """Get edges connecting the papers mentioned in the hypothesis."""
        relevant = []
        for e in self.edges:
            src = e.get('source_unit', '')
            tgt = e.get('target_unit', '')
            if src in unit_ids or tgt in unit_ids:
                relevant.append(e)
        return relevant[:10]  # cap for context length

    def assemble(self, hyp):
        """Assemble a rich context package for one hypothesis."""
        ctx = {
            'hypothesis_id': hyp.get('hypothesis_id', '?'),
            'type': hyp['type'],
            'original_description': hyp['description'],
            'v02_score': hyp.get('score', 0),
            'source_papers': hyp.get('source_papers', []),
        }

        ev = hyp.get('evidence', {})

        # SHARED type has evidence as list; flatten to dict
        if isinstance(ev, list) and len(ev) > 0:
            ev = ev[0] if isinstance(ev[0], dict) else {}

        if not isinstance(ev, dict):
            ev = {}

        # Extract referenced nodes based on hypothesis type
        node_refs = []
        for key in ['node_a', 'node_b', 'pole_a_node', 'pole_b_node']:
            val = ev.get(key)
            if isinstance(val, dict) and 'node_id' in val:
                node_refs.append(val)
            elif isinstance(val, str) and val in self.node_map:
                node_refs.append({'node_id': val,
                                  'content': self.node_map[val]['content'][:500],
                                  'tags': self.node_map[val].get('tags', [])})

        ctx['referenced_nodes'] = node_refs

        # Add shared keywords/tags
        if 'shared_keywords' in ev:
            ctx['shared_keywords'] = ev['shared_keywords']
        if 'shared_tag' in ev:
            ctx['shared_tag'] = ev['shared_tag']
        if 'shared_tags' in ev:
            ctx['shared_tags'] = ev['shared_tags']
        if 'axis' in ev:
            ctx['contradiction_axis'] = ev['axis']

        # Add role information
        if 'role_a' in ev:
            ctx['role_a'] = ev['role_a']
        if 'role_b' in ev:
            ctx['role_b'] = ev['role_b']

        # Add paper-level context (top nodes from each paper)
        paper_context = {}
        for uid in hyp.get('source_papers', []):
            paper_context[uid] = self.get_paper_nodes(uid, limit=3)
        ctx['paper_context'] = paper_context

        # Add related edges
        ctx['related_edges'] = self.get_related_edges(hyp.get('source_papers', []))

        # Add superiority/limitation cues for contradictions
        if 'superiority_cue_present' in ev:
            ctx['superiority_cue'] = ev['superiority_cue_present']
        if 'limitation_cue_present' in ev:
            ctx['limitation_cue'] = ev['limitation_cue_present']

        return ctx


# ═══════════════════════════════════════════════════════════════════
#  Semantic Clustering — group similar hypotheses
# ═══════════════════════════════════════════════════════════════════

class HypothesisClusterer:
    """Groups similar hypotheses to avoid redundant LLM calls."""

    def __init__(self, hypotheses):
        self.hypotheses = hypotheses

    def _jaccard(self, set_a, set_b):
        if not set_a and not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0

    def _hyp_signature(self, hyp):
        """Extract a signature for similarity comparison."""
        ev = hyp.get('evidence', {})
        papers = frozenset(hyp.get('source_papers', []))

        # Collect tags/keywords
        tags = set()
        if isinstance(ev, dict):
            for k in ['shared_keywords', 'shared_tags']:
                v = ev.get(k, [])
                if isinstance(v, list):
                    tags.update(v)
            if 'shared_tag' in ev:
                tags.add(ev['shared_tag'])
            if 'axis' in ev:
                tags.add(ev['axis'])

        # Add type
        tags.add(hyp['type'])

        return (papers, tags)

    def cluster(self, paper_threshold=0.5, tag_threshold=0.3):
        """
        Greedy clustering: two hypotheses belong to the same cluster if they
        share at least one source paper AND have sufficient tag overlap.
        """
        signatures = [self._hyp_signature(h) for h in self.hypotheses]
        n = len(self.hypotheses)
        assigned = [-1] * n  # -1 = unassigned
        cluster_id = 0

        for i in range(n):
            if assigned[i] != -1:
                continue
            assigned[i] = cluster_id
            papers_i, tags_i = signatures[i]

            for j in range(i + 1, n):
                if assigned[j] != -1:
                    continue
                papers_j, tags_j = signatures[j]

                # Must share at least one paper
                shared_papers = papers_i & papers_j
                if not shared_papers:
                    continue

                paper_sim = len(shared_papers) / max(len(papers_i | papers_j), 1)
                tag_sim = self._jaccard(tags_i, tags_j)

                if paper_sim >= paper_threshold or (shared_papers and tag_sim >= tag_threshold):
                    assigned[j] = cluster_id

            cluster_id += 1

        # Group by cluster
        clusters = defaultdict(list)
        for i, cid in enumerate(assigned):
            clusters[cid].append(i)

        return clusters, cluster_id


# ═══════════════════════════════════════════════════════════════════
#  Layer 3: Type-Specific Prompt Templates
# ═══════════════════════════════════════════════════════════════════

PROMPT_TEMPLATES = {
    'method_gap': """You are a research hypothesis analyst. Paper A has methods/concepts that Paper B lacks. Your job is to propose a SPECIFIC method transfer and predict the expected benefit and bottleneck.

## Methods Available in Paper A ({paper_a_id}) but Missing from Paper B
{paper_a_context}

## Paper B ({paper_b_id}) Current State
{paper_b_context}

## Detected Gap
{original_description}

## Task
Propose the MOST IMPACTFUL method transfer from Paper A to Paper B. Focus on 1-3 key methods, not all gaps. Consider:
1. Which method/concept from Paper A would most benefit Paper B's research goals?
2. What adaptations are needed for the transfer?
3. What is the main bottleneck?
4. What metric would demonstrate success?

Output JSON:
```json
{{
  "research_question": "<One clear, specific question about transferring the method>",
  "hypothesis": "<A falsifiable statement about the expected improvement>",
  "proposed_experiment": {{
    "setup": "<What to build/run>",
    "metric": "<What to measure>",
    "baseline": "<What to compare against>",
    "expected_bottleneck": "<Main adaptation difficulty>"
  }},
  "novelty": "high|medium|low",
  "feasibility": "high|medium|low",
  "impact": "<Why this transfer matters>"
}}
```""",

    'shared_keywords_no_connection': """You are a research hypothesis analyst. Two papers share keywords but have no direct methodological connection detected. Your job is to determine whether there is a DEEPER, non-obvious connection worth investigating.

## Shared Keywords
{shared_keywords}

## Paper A Context ({paper_a_id})
{paper_a_context}

## Paper B Context ({paper_b_id})
{paper_b_context}

## Detected Pattern
{original_description}

## Task
Analyze the shared keywords and propose a SPECIFIC, TESTABLE research question. Consider:
1. Is the shared keyword coincidental (e.g., both use "distillation" but for different purposes) or does it hint at a deeper structural similarity?
2. If there IS a deeper connection, what testable prediction follows?
3. What experiment would distinguish "deeper connection" from "coincidental keyword overlap"?

Output JSON:
```json
{{
  "research_question": "<One clear, specific question>",
  "hypothesis": "<A falsifiable statement>",
  "connection_type": "deep_structural | superficial_coincidence | partially_related",
  "proposed_experiment": {{
    "setup": "<What to build/run>",
    "metric": "<What to measure>",
    "baseline": "<What to compare against>",
    "expected_bottleneck": "<Main difficulty>"
  }},
  "novelty": "high|medium|low",
  "feasibility": "high|medium|low",
  "impact": "<Why this matters if true>"
}}
```""",

    'cross_domain_bridge': """You are a research hypothesis analyst. Two nodes from different domain roles share a common tag, suggesting a potential cross-domain transfer opportunity. Your job is to propose a concrete method transfer.

## Shared Concept
{shared_tag}

## Domain A ({role_a}, {paper_a_id})
{paper_a_context}

## Domain B ({role_b}, {paper_b_id})
{paper_b_context}

## Detected Pattern
{original_description}

## Task
Propose a SPECIFIC method or concept transfer between these domains. Consider:
1. What technique from Domain A could benefit Domain B (or vice versa)?
2. What adaptations would be needed?
3. What is the main bottleneck in making this transfer work?
4. What metric would demonstrate success?

Output JSON:
```json
{{
  "research_question": "<One clear, specific question about transferring the concept>",
  "hypothesis": "<A falsifiable statement about the transfer>",
  "transfer_direction": "A_to_B | B_to_A | bidirectional",
  "proposed_experiment": {{
    "setup": "<What to build/run>",
    "metric": "<What to measure>",
    "baseline": "<What to compare against>",
    "expected_bottleneck": "<Main adaptation difficulty>"
  }},
  "novelty": "high|medium|low",
  "feasibility": "high|medium|low",
  "impact": "<Why this transfer matters>"
}}
```""",

    'concept_contradiction': """You are a research hypothesis analyst. Two papers appear to advocate COMPETING approaches. Your job is to identify the ROOT CAUSE of the contradiction and propose a definitive arbitration experiment.

## Contradiction Axis
{contradiction_axis}

## Paper A Position ({paper_a_id})
{paper_a_context}

## Paper B Position ({paper_b_id})
{paper_b_context}

## Detected Pattern
{original_description}

## Additional Signals
- Superiority claim detected: {superiority_cue}
- Limitation acknowledgement detected: {limitation_cue}

## Task
Analyze this contradiction and propose an experiment that would RESOLVE it. Consider:
1. Are these truly contradictory, or are they addressing different regimes/conditions?
2. What hidden variable or condition could explain why both seem to work?
3. What single experiment would definitively favor one approach over the other?
4. Is there a unified framework that reconciles both?

Output JSON:
```json
{{
  "research_question": "<One clear question about the contradiction>",
  "hypothesis": "<A falsifiable statement about which approach wins under what conditions>",
  "root_cause_analysis": "<Why the contradiction exists>",
  "proposed_experiment": {{
    "setup": "<What to build/run>",
    "metric": "<What to measure>",
    "baseline": "<What to compare against>",
    "expected_bottleneck": "<Main difficulty>"
  }},
  "reconciliation_possible": true,
  "unified_framework_hint": "<If yes, what could unify both>",
  "novelty": "high|medium|low",
  "feasibility": "high|medium|low",
  "impact": "<Why resolving this matters>"
}}
```""",
}


def render_prompt(template, ctx):
    """Render a prompt template with context data."""
    # Extract paper IDs
    papers = ctx.get('source_papers', [])
    paper_a_id = papers[0] if len(papers) > 0 else 'unknown'
    paper_b_id = papers[1] if len(papers) > 1 else 'unknown'

    # Format paper contexts
    def format_nodes(node_list):
        if not node_list:
            return "[No node data available]"
        parts = []
        for n in node_list[:3]:
            content = n.get('content', n.get('node_id', ''))
            if isinstance(content, str) and len(content) > 400:
                content = content[:400] + "..."
            parts.append(f"  [{n.get('node_id', '?')}] ({n.get('node_type', '?')}): {content}")
        return '\n'.join(parts)

    # Build referenced nodes for each paper
    ref_nodes = ctx.get('referenced_nodes', [])
    paper_ctx = ctx.get('paper_context', {})

    paper_a_context = format_nodes(paper_ctx.get(paper_a_id, ref_nodes[:2]))
    paper_b_context = format_nodes(paper_ctx.get(paper_b_id, ref_nodes[2:4]))

    # Build template variables
    kwargs = {
        'paper_a_id': paper_a_id,
        'paper_b_id': paper_b_id,
        'paper_a_context': paper_a_context,
        'paper_b_context': paper_b_context,
        'original_description': ctx.get('original_description', ''),
        'shared_keywords': ', '.join(ctx.get('shared_keywords', ctx.get('shared_tags', []))),
        'shared_tag': ctx.get('shared_tag', 'world_model'),
        'shared_tags': ', '.join(ctx.get('shared_tags', [])),
        'contradiction_axis': ctx.get('contradiction_axis', 'N/A'),
        'role_a': ctx.get('role_a', 'unknown'),
        'role_b': ctx.get('role_b', 'unknown'),
        'superiority_cue': ctx.get('superiority_cue', 'Not detected'),
        'limitation_cue': ctx.get('limitation_cue', 'Not detected'),
    }

    return template.format(**kwargs)


# ═══════════════════════════════════════════════════════════════════
#  Main Pipeline
# ═══════════════════════════════════════════════════════════════════

def load_jsonl(path):
    items = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
    return items


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Hypothesis Reconstructor v0.3')
    parser.add_argument('library_dir', help='Library directory with v0.2 results')
    parser.add_argument('--cluster', action='store_true', default=True,
                        help='Enable semantic clustering (default: on)')
    parser.add_argument('--no-cluster', dest='cluster', action='store_false',
                        help='Disable clustering, process each hypothesis individually')
    parser.add_argument('--output', default=None,
                        help='Output JSONL file for prompt packages')
    parser.add_argument('--max-per-cluster', type=int, default=5,
                        help='Max hypotheses to sample per cluster for LLM processing')
    args = parser.parse_args()

    lib_dir = args.library_dir

    # Load data
    nodes = load_jsonl(os.path.join(lib_dir, 'node_store.jsonl'))
    edges = load_jsonl(os.path.join(lib_dir, 'graph_edges.jsonl'))
    catalog_path = os.path.join(lib_dir, 'source_catalog.jsonl')
    catalog = load_jsonl(catalog_path) if os.path.exists(catalog_path) else []

    hyp_path = os.path.join(lib_dir, 'hypothesis_results_v02.json')
    with open(hyp_path, 'r', encoding='utf-8') as f:
        hypotheses = json.load(f)

    print("=" * 70)
    print("  Hypothesis Reconstructor v0.3")
    print("=" * 70)
    print(f"  Library:       {lib_dir}")
    print(f"  Nodes:         {len(nodes)}")
    print(f"  Edges:         {len(edges)}")
    print(f"  Hypotheses:    {len(hypotheses)}")

    # Layer 2: Context Assembly
    print("\n--- Layer 2: Semantic Context Assembly ---")
    assembler = ContextAssembler(nodes, edges, catalog)
    contexts = [assembler.assemble(h) for h in hypotheses]
    print(f"  Context packages assembled: {len(contexts)}")

    # Clustering
    if args.cluster:
        print("\n--- Semantic Clustering ---")
        clusterer = HypothesisClusterer(hypotheses)
        clusters, n_clusters = clusterer.cluster()
        print(f"  Clusters formed: {n_clusters} (from {len(hypotheses)} hypotheses)")
        cluster_sizes = sorted([len(v) for v in clusters.values()], reverse=True)
        print(f"  Largest clusters: {cluster_sizes[:10]}")
        print(f"  Avg cluster size: {len(hypotheses)/n_clusters:.1f}")
    else:
        n_clusters = len(hypotheses)
        clusters = {i: [i] for i in range(len(hypotheses))}

    # Layer 3: Prompt Generation
    print("\n--- Layer 3: Prompt Generation ---")
    prompt_packages = []

    for cid, member_indices in clusters.items():
        # Sample top hypotheses from cluster (highest v0.2 score)
        cluster_hyps = [(i, hypotheses[i]) for i in member_indices]
        cluster_hyps.sort(key=lambda x: x[1].get('score', 0), reverse=True)

        # Take top N from cluster
        sampled = cluster_hyps[:args.max_per_cluster]

        for idx, hyp in sampled:
            ctx = contexts[idx]
            template = PROMPT_TEMPLATES.get(hyp['type'])
            if not template:
                continue

            prompt = render_prompt(template, ctx)

            prompt_packages.append({
                'prompt_id': f"P{len(prompt_packages)+1:04d}",
                'cluster_id': cid,
                'hypothesis_id': ctx['hypothesis_id'],
                'hypothesis_type': ctx['type'],
                'v02_score': ctx['v02_score'],
                'source_papers': ctx['source_papers'],
                'prompt': prompt,
                # Pre-filled context for grounding check later
                'context_snapshot': {
                    'referenced_node_ids': [n.get('node_id') for n in ctx.get('referenced_nodes', [])],
                    'shared_keywords': ctx.get('shared_keywords', ctx.get('shared_tags', [])),
                    'shared_tag': ctx.get('shared_tag'),
                    'contradiction_axis': ctx.get('contradiction_axis'),
                }
            })

    print(f"  Prompt packages generated: {len(prompt_packages)}")
    by_type = Counter(p['hypothesis_type'] for p in prompt_packages)
    for t, c in by_type.most_common():
        print(f"    {t:45s} {c}")

    # Write output
    out_path = args.output or os.path.join(lib_dir, 'v03_prompts.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for pkg in prompt_packages:
            f.write(json.dumps(pkg, ensure_ascii=False) + '\n')

    print(f"\n  Output written to: {out_path}")
    print("=" * 70)

    # Also write cluster map for later analysis
    cluster_map_path = os.path.join(lib_dir, 'v03_cluster_map.json')
    cluster_summary = {}
    for cid, members in clusters.items():
        member_hyps = [hypotheses[i] for i in members]
        types_in_cluster = Counter(h['type'] for h in member_hyps)
        cluster_summary[cid] = {
            'size': len(members),
            'types': dict(types_in_cluster),
            'top_score': max(h.get('score', 0) for h in member_hyps),
            'member_ids': [h.get('hypothesis_id', '?') for h in member_hyps],
        }
    with open(cluster_map_path, 'w', encoding='utf-8') as f:
        json.dump(cluster_summary, f, indent=2, ensure_ascii=False)
    print(f"  Cluster map written to: {cluster_map_path}")


if __name__ == '__main__':
    main()
