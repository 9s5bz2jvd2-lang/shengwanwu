#!/usr/bin/env python3
"""
venus_physics_hypotheses.py — Physics-oriented hypothesis generation for Venus
================================================================================

Instead of v0.2's mechanical pattern detection (method_gap, cross_domain),
this script generates physics/chemistry-oriented research hypotheses directly
from limitation nodes, open questions, and uncertain findings.

Approach:
1. Extract limitation + uncertainty nodes as hypothesis seeds
2. For each seed, find related nodes (same tags) from other papers
3. Generate a physics-oriented prompt asking:
   - What is the physical/chemical mechanism?
   - What simulation could test it?
   - What observation could confirm/falsify it?

Usage:
  python venus_physics_hypotheses.py <library_dir> [--output prompts.jsonl]
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path


def load_jsonl(path):
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except:
                    pass
    return items


def find_related_nodes(seed, all_nodes, max_related=4):
    """Find nodes from OTHER papers that share tags with the seed."""
    seed_tags = set(t.lower() for t in seed.get('tags', []))
    seed_unit = seed.get('unit_id', '')
    
    related = []
    for n in all_nodes:
        if n.get('unit_id') == seed_unit:
            continue
        n_tags = set(t.lower() for t in n.get('tags', []))
        overlap = seed_tags & n_tags
        if overlap:
            related.append((len(overlap), n))
    
    related.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in related[:max_related]]


# Physics-oriented prompt templates
PHYSICS_PROMPT_TEMPLATE = """You are a planetary science research hypothesis generator. Your job is to formulate a SPECIFIC, PHYSICALLY-MEANINGFUL research hypothesis about Venus cloud phenomena — NOT a method transfer proposal.

## Open Question / Limitation (from {seed_paper})
{seed_content}

## Related Evidence from Other Papers
{related_evidence}

## Context
- This question comes from a systematic review of {n_papers} Venus cloud research papers
- The hypothesis should address the PHYSICAL or CHEMICAL mechanism, not methodology
- It should propose BOTH a simulation approach AND an observational/experimental test

## Task
Formulate ONE research hypothesis that:
1. Addresses a genuine physical/chemical question about Venus clouds
2. Proposes a specific mechanism or explanation
3. Can be tested by BOTH simulation AND observation
4. Is falsifiable — state what evidence would disprove it

Output JSON:
```json
{{
  "research_question": "<A question about a physical/chemical phenomenon, NOT about methods>",
  "hypothesis": "<A specific, falsifiable statement about the mechanism>",
  "physical_mechanism": "<The proposed physical/chemical explanation>",
  "simulation_approach": {{
    "method": "<Specific simulation: DFT, MD, GCM, radiative transfer, etc.>",
    "setup": "<What to simulate specifically>",
    "expected_output": "<What the simulation would show if hypothesis is true>"
  }},
  "observation_approach": {{
    "method": "<Specific observation: spectral, in-situ, remote sensing, laboratory>",
    "target": "<What to measure>",
    "instrument": "<Suggested instrument or technique>",
    "expected_signal": "<What the observation would show if hypothesis is true>"
  }},
  "falsification": "<What evidence would disprove this hypothesis>",
  "novelty": "high|medium|low",
  "feasibility": "high|medium|low",
  "impact": "<Why this matters for understanding Venus>"
}}
```"""


def generate_prompts(nodes, catalog):
    """Generate physics-oriented prompts from limitation/uncertainty nodes."""
    
    # Identify hypothesis seeds: limitation nodes + uncertainty-containing findings
    seeds = []
    
    # All limitation nodes
    for n in nodes:
        if n.get('node_type') == 'limitation':
            seeds.append(n)
    
    # Findings with uncertainty markers
    uncertainty_words = ['unknown', 'unclear', 'not yet', 'mystery', 'unresolved',
                         'debate', 'controversy', 'uncertain', 'remain', 'poorly constrained',
                         'future work', 'needs further', 'not well understood']
    
    for n in nodes:
        if n.get('node_type') in ('finding', 'result'):
            content_lower = n.get('content', '').lower()
            if any(w in content_lower for w in uncertainty_words):
                seeds.append(n)
    
    # Also extract concept nodes about key Venus mysteries
    mystery_tags = {'UV_absorber', 'UV_absorber_candidate', 'habitability', 
                    'life_hypothesis', 'phosphine', 'detection_controversy',
                    'slow_wind_origin', 'convective_oscillation'}
    for n in nodes:
        if n.get('node_type') in ('concept', 'relationship'):
            n_tags = set(t.lower() for t in n.get('tags', []))
            if n_tags & mystery_tags:
                seeds.append(n)
    
    # Deduplicate seeds by content similarity
    seen_content = []
    unique_seeds = []
    for s in seeds:
        content_key = s.get('content', '')[:100].lower()
        is_dup = False
        for sc in seen_content:
            if content_key[:50] in sc or sc[:50] in content_key:
                is_dup = True
                break
        if not is_dup:
            seen_content.append(content_key)
            unique_seeds.append(s)
    
    # Generate prompts
    prompts = []
    n_papers = len(set(n.get('unit_id') for n in nodes))
    
    # Paper title lookup
    paper_titles = {}
    for c in catalog:
        paper_titles[c['unit_id']] = c.get('title', c['unit_id'])
    
    for seed in unique_seeds:
        related = find_related_nodes(seed, nodes, max_related=3)
        
        # Format related evidence
        related_parts = []
        for r in related:
            title = paper_titles.get(r.get('unit_id', ''), r.get('unit_id', ''))
            content = r.get('content', '')[:300]
            related_parts.append(f"  [{r.get('node_id')}] from \"{title}\":\n  {content}")
        
        related_text = '\n\n'.join(related_parts) if related_parts else "  [No directly related nodes from other papers]"
        
        seed_title = paper_titles.get(seed.get('unit_id', ''), seed.get('unit_id', ''))
        seed_content = seed.get('content', '')[:500]
        
        prompt = PHYSICS_PROMPT_TEMPLATE.format(
            seed_paper=seed_title,
            seed_content=seed_content,
            related_evidence=related_text,
            n_papers=n_papers,
        )
        
        prompts.append({
            'prompt_id': f"PHY-{len(prompts)+1:03d}",
            'seed_node_id': seed.get('node_id', '?'),
            'seed_unit_id': seed.get('unit_id', '?'),
            'seed_type': seed.get('node_type', '?'),
            'seed_tags': seed.get('tags', []),
            'prompt': prompt,
            'related_node_ids': [r.get('node_id') for r in related],
        })
    
    return prompts


def main():
    lib_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    
    nodes = load_jsonl(os.path.join(lib_dir, 'node_store.jsonl'))
    catalog = load_jsonl(os.path.join(lib_dir, 'source_catalog.jsonl'))
    
    print(f"Loaded {len(nodes)} nodes from {len(catalog)} papers")
    
    prompts = generate_prompts(nodes, catalog)
    
    print(f"\nGenerated {len(prompts)} physics-oriented prompts")
    by_type = defaultdict(int)
    for p in prompts:
        by_type[p['seed_type']] += 1
    for t, c in sorted(by_type.items()):
        print(f"  {t}: {c}")
    
    out_path = os.path.join(lib_dir, 'venus_physics_prompts.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for p in prompts:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')
    
    print(f"\nWritten to {out_path}")
    
    # Show sample
    if prompts:
        print(f"\n=== Sample prompt (first 500 chars) ===")
        print(prompts[0]['prompt'][:500])


if __name__ == '__main__':
    main()
