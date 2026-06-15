#!/usr/bin/env python3
"""
v03_collect_and_rank.py — Collect v0.3 results, deduplicate, rank
=================================================================

Phase 4: Grounding check — verify referenced methods exist in source nodes
Phase 5: Priority sort → Top-20 actionable research questions

Usage:
  python v03_collect_and_rank.py <library_dir>
"""

import json
import os
import re
import sys
from collections import defaultdict


def load_jsonl(path):
    items = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return items


def collect_results(lib_dir):
    """Collect all batch result files."""
    all_results = []
    for i in range(1, 10):
        path = os.path.join(lib_dir, f'v03_results_batch_{i}.jsonl')
        batch = load_jsonl(path)
        if batch:
            print(f"  Batch {i}: {len(batch)} results")
            all_results.extend(batch)
    return all_results


def deduplicate(results):
    """Remove near-duplicate research questions."""
    seen = []
    unique = []
    for r in results:
        rq = r.get('research_question', '').lower().strip()
        is_dup = False
        for s in seen:
            # Simple similarity: shared words ratio
            words_rq = set(rq.split())
            words_s = set(s.split())
            if not words_rq or not words_s:
                continue
            overlap = len(words_rq & words_s) / max(len(words_rq | words_s), 1)
            if overlap > 0.7:
                is_dup = True
                break
        if not is_dup:
            seen.append(rq)
            unique.append(r)
    return unique


def grounding_check(result, node_store):
    """Check if referenced methods/concepts exist in source nodes."""
    rq = result.get('research_question', '')
    hyp = result.get('hypothesis', '')
    text = (rq + ' ' + hyp).lower()
    
    # Get source paper nodes
    source_papers = result.get('source_papers', [])
    node_text = ''
    for n in node_store:
        if n.get('unit_id') in source_papers:
            node_text += ' ' + n.get('content', '').lower()
    
    if not node_text:
        return 0.5  # neutral if no source nodes
    
    # Check how many key terms from the RQ appear in source node text
    key_terms = [w for w in re.findall(r'\b[a-z_]{4,}\b', text)
                 if w not in {'the', 'this', 'that', 'with', 'from', 'would', 'could',
                              'should', 'whether', 'between', 'using', 'based', 'these',
                              'those', 'their', 'which', 'what', 'when', 'where', 'more',
                              'such', 'also', 'than', 'have', 'been', 'were', 'they',
                              'them', 'each', 'other', 'some', 'into', 'over', 'under',
                              'most', 'only', 'very', 'will', 'can', 'may', 'how', 'why',
                              'are', 'was', 'has', 'had', 'its', 'any', 'all', 'one',
                              'two', 'not', 'but', 'for', 'and', 'nor'}]
    
    if not key_terms:
        return 0.5
    
    grounded = sum(1 for t in key_terms if t in node_text)
    return min(grounded / len(key_terms), 1.0)


NOVELTY_MAP = {'high': 1.0, 'medium': 0.5, 'low': 0.2}
FEASIBILITY_MAP = {'high': 1.0, 'medium': 0.5, 'low': 0.2}


def compute_priority(result, grounding_score):
    """Compute priority: 0.4*novelty + 0.3*impact_proxy + 0.3*feasibility.
    
    Impact proxy: grounded from text quality (length + specificity).
    """
    novelty = NOVELTY_MAP.get(result.get('novelty', 'medium').lower(), 0.5)
    feasibility = FEASIBILITY_MAP.get(result.get('feasibility', 'medium').lower(), 0.5)
    
    # Impact proxy: longer, more specific impact descriptions = higher
    impact_text = result.get('impact', '')
    impact_score = min(len(impact_text) / 200.0, 1.0)
    
    # Grounding penalty
    grounding = grounding_score
    
    priority = (0.4 * novelty + 0.3 * impact_score + 0.3 * feasibility) * (0.5 + 0.5 * grounding)
    
    return {
        'novelty_score': novelty,
        'impact_score': round(impact_score, 3),
        'feasibility_score': feasibility,
        'grounding_score': round(grounding_score, 3),
        'priority': round(priority, 4),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python v03_collect_and_rank.py <library_dir>")
        sys.exit(1)

    lib_dir = sys.argv[1]

    print("=" * 70)
    print("  v0.3 Collect & Rank")
    print("=" * 70)

    # Collect results
    print("\n--- Collecting batch results ---")
    results = collect_results(lib_dir)
    print(f"  Total results: {len(results)}")

    if not results:
        print("  No results found! Check if daemons completed.")
        sys.exit(1)

    # Load node store for grounding
    node_store = load_jsonl(os.path.join(lib_dir, 'node_store.jsonl'))

    # Deduplicate
    print("\n--- Deduplication ---")
    unique = deduplicate(results)
    print(f"  Before: {len(results)}, After: {len(unique)}, Removed: {len(results) - len(unique)}")

    # Grounding check + priority
    print("\n--- Grounding Check & Priority Scoring ---")
    scored = []
    for r in unique:
        gs = grounding_check(r, node_store)
        pri = compute_priority(r, gs)
        r['scoring'] = pri
        scored.append(r)

    # Sort by priority
    scored.sort(key=lambda x: x['scoring']['priority'], reverse=True)

    # Top 20
    top_20 = scored[:20]
    print(f"\n--- Top 20 Research Questions ---")
    for i, r in enumerate(top_20, 1):
        print(f"\n  #{i} [P={r['scoring']['priority']:.3f}] [{r.get('hypothesis_type', '?')}]")
        print(f"  Q: {r.get('research_question', '?')[:200]}")
        print(f"  Novelty: {r.get('novelty', '?')}, Feasibility: {r.get('feasibility', '?')}")
        print(f"  Grounding: {r['scoring']['grounding_score']:.2f}")

    # Write full output
    out_path = os.path.join(lib_dir, 'v03_ranked_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'total_raw': len(results),
            'total_unique': len(unique),
            'top_20': top_20,
            'all_scored': scored,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Full results: {out_path}")

    # Write Top-20 summary as markdown
    md_path = os.path.join(lib_dir, 'v03_top20_research_questions.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# V0.3 Top-20 Actionable Research Questions\n\n")
        f.write(f"*Generated from {len(results)} raw hypotheses → {len(unique)} unique → ranked*\n\n")
        for i, r in enumerate(top_20, 1):
            f.write(f"## #{i} — {r.get('research_question', 'N/A')}\n\n")
            f.write(f"**Type:** {r.get('hypothesis_type', '?')}  \n")
            f.write(f"**Priority:** {r['scoring']['priority']:.3f}  \n")
            f.write(f"**Novelty:** {r.get('novelty', '?')} | **Feasibility:** {r.get('feasibility', '?')} | **Grounding:** {r['scoring']['grounding_score']:.2f}\n\n")
            f.write(f"**Hypothesis:** {r.get('hypothesis', 'N/A')}\n\n")
            exp = r.get('proposed_experiment', {})
            if isinstance(exp, dict):
                f.write(f"**Proposed Experiment:**\n")
                f.write(f"- Setup: {exp.get('setup', 'N/A')}\n")
                f.write(f"- Metric: {exp.get('metric', 'N/A')}\n")
                f.write(f"- Baseline: {exp.get('baseline', 'N/A')}\n")
                f.write(f"- Bottleneck: {exp.get('expected_bottleneck', 'N/A')}\n\n")
            f.write(f"**Impact:** {r.get('impact', 'N/A')}\n\n")
            f.write(f"---\n\n")
    print(f"  Top-20 markdown: {md_path}")
    print("=" * 70)


if __name__ == '__main__':
    main()
