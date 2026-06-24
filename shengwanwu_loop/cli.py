#!/usr/bin/env python3
"""Minimal runnable hypothesis kernel for Shengwanwu Loop.

Stdlib-only v0.1:
- add: append distilled Markdown notes into a persistent knowledge_base.
- map: build a simple gap_map from the accumulated node_store.
- generate/hypothesize: generate hypotheses from gaps and crystallize a return contract.
- resume: resume from loop_state without rerunning completed earlier stages.

This is intentionally small and harness-agnostic: files in, JSONL/Markdown out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


STATE_ORDER = [
    "S1_DISTILL",
    "S2_MAP",
    "S3_GENERATE",
    "S5_CRYSTALLIZE",
    "S6_RETURN",
]

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "are", "was", "were",
    "研究", "方法", "结果", "结论", "问题", "可以", "需要", "通过", "基于", "一种", "这个", "那个",
}


# ---------- basic file helpers ----------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_kb(kb: Path) -> None:
    kb.mkdir(parents=True, exist_ok=True)
    for name in ["node_store.jsonl", "source_catalog.jsonl", "gap_map.jsonl", "hypothesis_log.jsonl"]:
        (kb / name).touch(exist_ok=True)


def ensure_run(out: Path) -> None:
    (out / "store").mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows: List[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL in {path}:{line_no}: {exc}")
    return rows


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[`*_#>\[\](){},.;:!?，。；：！？、\s]+", " ", text)
    return text.strip()


def iter_md_files(inputs: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files.extend(sorted(x for x in p.rglob("*.md") if x.is_file()))
        elif p.is_file() and p.suffix.lower() == ".md":
            files.append(p)
        elif p.is_file():
            print(f"[skip] only Markdown is supported in v0.1: {p}")
        else:
            print(f"[skip] not found: {p}")
    # stable unique order
    seen = set()
    uniq = []
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


# ---------- distillation ----------


def infer_node_type(text: str) -> str:
    t = text.lower()
    if "?" in t or "？" in t or any(k in t for k in ["open question", "问题", "未解", "待验证"]):
        return "question"
    if any(k in t for k in ["limitation", "limit", "boundary", "限制", "边界", "不足"]):
        return "limitation"
    if any(k in t for k in ["method", "workflow", "algorithm", "方法", "流程", "算法", "步骤"]):
        return "method"
    if any(k in t for k in ["finding", "result", "发现", "结果", "显示", "表明"]):
        return "finding"
    if any(k in t for k in ["gotcha", "pitfall", "坑", "误判", "失败"]):
        return "gotcha"
    if any(k in t for k in ["claim", "hypothesis", "假设", "主张"]):
        return "claim"
    return "concept"


def extract_tags(text: str, limit: int = 8) -> List[str]:
    # Works for both English-ish tokens and Chinese chunks separated by punctuation.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,8}", text)
    counts: Dict[str, int] = {}
    for tok in tokens:
        key = tok.lower()
        if key in STOPWORDS:
            continue
        counts[key] = counts.get(key, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
    return fallback


def extract_nodes_from_markdown(path: Path, source_id: str, source_hash: str, mock: bool = False) -> List[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title = extract_title(text, path.stem)

    candidates: List[Tuple[str, str]] = []
    current_heading = title
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_heading = line.lstrip("#").strip() or current_heading
            continue
        # prefer structured bullets, but accept substantial prose lines
        if line.startswith(('-', '*', '+')):
            content = line.lstrip("-*+ ").strip()
        elif re.match(r"^\d+[.)]\s+", line):
            content = re.sub(r"^\d+[.)]\s+", "", line).strip()
        elif len(line) >= 28:
            content = line
        else:
            continue
        if content:
            candidates.append((current_heading, content))

    if mock and not candidates:
        candidates = [
            (title, f"{title} contains an unresolved mechanism that may connect method and evidence."),
            (title, f"{title} reports a boundary condition that requires further validation."),
            (title, f"Open question: how does {title} generalize across contexts?"),
        ]

    nodes: List[dict] = []
    for idx, (heading, content) in enumerate(candidates, 1):
        node_id = f"N_{source_hash[:12]}_{idx:04d}"
        nodes.append({
            "node_id": node_id,
            "source_id": source_id,
            "source_path": str(path),
            "source_hash": source_hash,
            "node_index": idx,
            "heading": heading,
            "node_type": infer_node_type(content),
            "content": content,
            "evidence_span": {"section": heading, "quote": content[:500]},
            "confidence": "extracted",
            "tags": extract_tags(content),
            "created_at": utc_now(),
        })
    return nodes


def cmd_add(args: argparse.Namespace) -> int:
    kb = Path(args.kb)
    ensure_kb(kb)
    source_catalog = kb / "source_catalog.jsonl"
    node_store = kb / "node_store.jsonl"
    known_hashes = {row.get("content_hash") for row in read_jsonl(source_catalog)}

    files = iter_md_files(args.inputs)
    if not files:
        print("No Markdown files found.")
        return 1

    total_nodes = 0
    for path in files:
        content_hash = sha256_file(path)
        if content_hash in known_hashes:
            print(f"[skip] 已处理，跳过: {path}")
            continue
        source_id = f"S_{content_hash[:12]}"
        nodes = extract_nodes_from_markdown(path, source_id, content_hash, mock=args.mock)
        append_jsonl(node_store, nodes)
        append_jsonl(source_catalog, [{
            "source_id": source_id,
            "path": str(path),
            "hash_algorithm": "sha256",
            "content_hash": content_hash,
            "processed_at": utc_now(),
            "node_count": len(nodes),
            "input_contract": "distilled_markdown_or_plain_markdown",
        }])
        known_hashes.add(content_hash)
        total_nodes += len(nodes)
        print(f"[add] {path} -> {len(nodes)} nodes")

    print(f"[done] appended {total_nodes} nodes into {node_store}")
    return 0


# ---------- map gaps ----------


def shared_tags(a: dict, b: dict) -> List[str]:
    return sorted(set(a.get("tags", [])) & set(b.get("tags", [])))


def build_gaps(nodes: List[dict], max_gaps: int = 30) -> List[dict]:
    gaps: List[dict] = []
    gid = 1

    questions = [n for n in nodes if n.get("node_type") == "question"]
    for n in questions[:max_gaps]:
        gaps.append({
            "gap_id": f"G{gid:03d}",
            "type": "open_question",
            "description": f"Open question requires hypothesis: {n.get('content', '')[:180]}",
            "related_nodes": [n["node_id"]],
            "derivation": "Question-like node was extracted from distilled source material.",
            "evidence_boundary": "The source raises or implies a question; no answer is established in the current knowledge field.",
            "research_potential": "high",
            "created_at": utc_now(),
        })
        gid += 1

    # Contradiction-like signals.
    conflict_nodes = [n for n in nodes if re.search(r"矛盾|冲突|相反|contradict|conflict|however|but|但", n.get("content", ""), re.I)]
    for n in conflict_nodes[: max_gaps - len(gaps)]:
        gaps.append({
            "gap_id": f"G{gid:03d}",
            "type": "contradiction",
            "description": f"Potential contradiction or tension: {n.get('content', '')[:180]}",
            "related_nodes": [n["node_id"]],
            "derivation": "Conflict markers were detected in an extracted node.",
            "evidence_boundary": "The tension is lexical/structural in v0.1 and needs human or six-eyes validation.",
            "research_potential": "medium",
            "created_at": utc_now(),
        })
        gid += 1

    # Missing links between related but different-type nodes.
    for i, a in enumerate(nodes):
        if len(gaps) >= max_gaps:
            break
        for b in nodes[i + 1:]:
            if len(gaps) >= max_gaps:
                break
            if a.get("source_id") == b.get("source_id") and a.get("node_type") == b.get("node_type"):
                continue
            common = shared_tags(a, b)
            type_pair = {a.get("node_type"), b.get("node_type")}
            if common or ("method" in type_pair and ("finding" in type_pair or "limitation" in type_pair or "question" in type_pair)):
                desc = f"Possible missing link between {a.get('node_type')} node and {b.get('node_type')} node."
                if common:
                    desc += f" Shared tags: {', '.join(common[:5])}."
                gaps.append({
                    "gap_id": f"G{gid:03d}",
                    "type": "missing_link",
                    "description": desc,
                    "related_nodes": [a["node_id"], b["node_id"]],
                    "derivation": "Two nodes share tags or complementary roles but have no explicit edge in v0.1.",
                    "evidence_boundary": "The connection is a candidate gap, not an established fact.",
                    "research_potential": "medium" if common else "low",
                    "created_at": utc_now(),
                })
                gid += 1

    # If notes are sparse, still create a boundary gap so the user can run end-to-end.
    if not gaps and nodes:
        chosen = nodes[: min(2, len(nodes))]
        gaps.append({
            "gap_id": "G001",
            "type": "boundary_gap",
            "description": "The current knowledge field is too sparse; identify boundary conditions before stronger hypotheses.",
            "related_nodes": [n["node_id"] for n in chosen],
            "derivation": "Few explicit questions/links were found; sparse fields should first map boundaries.",
            "evidence_boundary": "Insufficient structure; generated hypotheses must be low-confidence.",
            "research_potential": "low",
            "created_at": utc_now(),
        })
    return gaps


def cmd_map(args: argparse.Namespace) -> int:
    kb = Path(args.kb)
    ensure_kb(kb)
    nodes = read_jsonl(kb / "node_store.jsonl")
    if not nodes:
        print(f"No nodes in {kb / 'node_store.jsonl'}; run add first.")
        return 1
    gaps = build_gaps(nodes, max_gaps=args.max_gaps)
    write_jsonl(kb / "gap_map.jsonl", gaps)
    if args.out:
        out = Path(args.out)
        ensure_run(out)
        write_jsonl(out / "store" / "gap_map.jsonl", gaps)
    print(f"[map] wrote {len(gaps)} gaps -> {kb / 'gap_map.jsonl'}")
    return 0


# ---------- generate hypotheses and crystallize ----------


def hypothesis_from_gap(gap: dict, nodes_by_id: Dict[str, dict], goal: str) -> dict:
    related = [nodes_by_id.get(nid, {}) for nid in gap.get("related_nodes", [])]
    snippets = [n.get("content", "")[:120] for n in related if n]
    basis = " / ".join(snippets) if snippets else gap.get("description", "")
    if gap.get("type") == "contradiction":
        claim = f"A hidden boundary condition may reconcile this tension: {gap.get('description', '')[:160]}"
        htype = "boundary_hypothesis"
    elif gap.get("type") == "open_question":
        claim = f"A testable mechanism may answer the open question by linking the observed node to a missing mediator: {basis[:160]}"
        htype = "mechanism_hypothesis"
    elif gap.get("type") == "missing_link":
        claim = f"The related nodes may be connected through an unobserved intermediate mechanism relevant to: {goal}"
        htype = "missing_link_hypothesis"
    else:
        claim = f"The current boundary gap may define a low-confidence hypothesis space for: {goal}"
        htype = "boundary_hypothesis"
    norm = normalize_text(gap.get("gap_id", "") + " " + claim)
    hid = "H_" + stable_hash(norm)[:12]
    return {
        "hypothesis_id": hid,
        "gap_id": gap.get("gap_id"),
        "type": htype,
        "claim": claim,
        "rationale": gap.get("derivation", gap.get("description", "")),
        "source_nodes": gap.get("related_nodes", []),
        "confidence": "类比" if gap.get("type") in {"missing_link", "boundary_gap"} else "猜想",
        "evidence_boundary": gap.get("evidence_boundary", "Needs validation."),
        "testability": "medium" if gap.get("research_potential") == "high" else "low",
        "status": "candidate",
        "dedupe_hash": stable_hash(norm),
        "created_at": utc_now(),
    }


def write_loop_state(out: Path, state: str, completed: List[str], kb: Path, artifacts: Dict[str, str], next_action: str = "") -> None:
    ensure_run(out)
    data = {
        "loop_id": out.name,
        "current_state": state,
        "completed_states": completed,
        "kb_path": str(kb),
        "artifacts": artifacts,
        "next_action": next_action,
        "updated_at": utc_now(),
    }
    (out / "loop_state.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def crystallize(out: Path, kb: Path, goal: str, hypotheses: Optional[List[dict]] = None) -> None:
    ensure_run(out)
    hyp_path = out / "store" / "hypothesis_candidates.jsonl"
    gap_path = out / "store" / "gap_map.jsonl"
    if hypotheses is None:
        hypotheses = read_jsonl(hyp_path)
    gaps = read_jsonl(gap_path) if gap_path.exists() else read_jsonl(kb / "gap_map.jsonl")

    artifact_cards = [
        {
            "artifact_id": "A_return_contract",
            "type": "return_contract",
            "title": f"Return contract for {out.name}",
            "path": str(out / "return_contract.md"),
            "source_loop": out.name,
            "reuse_level": "portable",
            "evidence_level": "traceable",
            "next_use": "Resume or branch the next Shengwanwu loop.",
        },
        {
            "artifact_id": "A_hypotheses",
            "type": "hypothesis_candidates",
            "title": "Gap-linked hypothesis candidates",
            "path": str(hyp_path),
            "source_loop": out.name,
            "reuse_level": "project_only",
            "evidence_level": "speculative",
            "next_use": "Run six-eyes validation or proof/experiment gate.",
        },
    ]
    write_jsonl(out / "artifact_cards.jsonl", artifact_cards)

    lines = [
        "# Return Contract",
        "",
        f"- loop_id: `{out.name}`",
        f"- goal: {goal}",
        f"- kb: `{kb}`",
        f"- updated_at: {utc_now()}",
        "",
        "## 已完成",
        "- S2 Map: gap map available" if gaps else "- S2 Map: no gap map found",
        f"- S3 Generate: {len(hypotheses)} hypothesis candidates",
        "- S5 Crystallize: artifact cards written",
        "",
        "## 关键产物",
        f"- gap map: `{gap_path if gap_path.exists() else kb / 'gap_map.jsonl'}`",
        f"- hypotheses: `{hyp_path}`",
        f"- artifact cards: `{out / 'artifact_cards.jsonl'}`",
        "",
        "## 证据边界",
        "- v0.1 输出为候选假设，不是已验证结论。",
        "- confidence 字段区分类比/猜想；后续须走六眼、证据门或实验门。",
        "",
        "## 假设摘要",
    ]
    for h in hypotheses[:20]:
        lines.append(f"- `{h.get('hypothesis_id')}` gap `{h.get('gap_id')}`: {h.get('claim')}")
    lines += [
        "",
        "## 下一轮入口",
        "1. 运行六眼验证：证眼/源眼/构眼/隙眼/界眼/生眼。",
        "2. 对 high-value 假设建立 experiment/proof loop。",
        "3. 将稳定方法结晶为 skill/schema/prompt。",
        "",
        "## 不要重复做",
        "- 不要重复 add 已在 source_catalog 中登记的同一 source hash。",
        "- 不要把 candidate hypothesis 写成 verified conclusion。",
    ]
    (out / "return_contract.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_loop_state(out, "S6_RETURN", ["S2_MAP", "S3_GENERATE", "S5_CRYSTALLIZE", "S6_RETURN"], kb, {
        "gap_map": str(gap_path if gap_path.exists() else kb / "gap_map.jsonl"),
        "hypotheses": str(hyp_path),
        "artifact_cards": str(out / "artifact_cards.jsonl"),
        "return_contract": str(out / "return_contract.md"),
    }, next_action="validate hypotheses or propagate next loops")


def cmd_generate(args: argparse.Namespace) -> int:
    kb = Path(args.kb)
    out = Path(args.out)
    ensure_kb(kb)
    ensure_run(out)

    nodes = read_jsonl(kb / "node_store.jsonl")
    if not nodes:
        print(f"No nodes in {kb / 'node_store.jsonl'}; run add first.")
        return 1

    gaps = read_jsonl(kb / "gap_map.jsonl")
    if not gaps or args.remap:
        print("[generate] gap_map missing/empty or --remap set; running map first.")
        gaps = build_gaps(nodes, max_gaps=args.max_gaps)
        write_jsonl(kb / "gap_map.jsonl", gaps)

    # Snapshot the exact gap map used by this run.
    write_jsonl(out / "store" / "gap_map.jsonl", gaps)
    nodes_by_id = {n["node_id"]: n for n in nodes if "node_id" in n}

    existing_hashes = {h.get("dedupe_hash") for h in read_jsonl(kb / "hypothesis_log.jsonl")}
    run_hypotheses: List[dict] = []
    new_for_global: List[dict] = []
    for gap in gaps[: args.max_hypotheses]:
        h = hypothesis_from_gap(gap, nodes_by_id, args.goal)
        if h["dedupe_hash"] not in existing_hashes:
            new_for_global.append(h)
            existing_hashes.add(h["dedupe_hash"])
        run_hypotheses.append(h)

    write_jsonl(out / "store" / "hypothesis_candidates.jsonl", run_hypotheses)
    append_jsonl(kb / "hypothesis_log.jsonl", new_for_global)
    crystallize(out, kb, args.goal, run_hypotheses)
    print(f"[generate] wrote {len(run_hypotheses)} run hypotheses -> {out / 'store' / 'hypothesis_candidates.jsonl'}")
    print(f"[generate] appended {len(new_for_global)} new global hypotheses -> {kb / 'hypothesis_log.jsonl'}")
    print(f"[return] {out / 'return_contract.md'}")
    return 0


# ---------- resume ----------


def cmd_resume(args: argparse.Namespace) -> int:
    out = Path(args.run_dir)
    state_path = out / "loop_state.json"
    if not state_path.exists():
        print(f"No loop_state.json found: {state_path}")
        return 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current = state.get("current_state", "")
    kb = Path(args.kb or state.get("kb_path", "knowledge_base"))
    goal = args.goal or state.get("goal") or "resume hypothesis loop"
    print(f"[resume] current_state={current} kb={kb}")

    # Verify artifacts before trusting state.
    artifacts = state.get("artifacts", {}) or {}
    for name, p in artifacts.items():
        path = Path(p)
        if not path.exists() or (path.is_file() and path.stat().st_size == 0):
            print(f"[warn] artifact missing/empty for {name}: {path}")

    if current in {"S1_DISTILL", "S2_MAP"}:
        # Do not rerun add; map/generate can use the persistent kb.
        ns = argparse.Namespace(kb=str(kb), out=str(out), goal=goal, max_hypotheses=args.max_hypotheses, max_gaps=args.max_gaps, remap=(current == "S1_DISTILL"), mock=args.mock)
        return cmd_generate(ns)
    if current == "S3_GENERATE":
        print("[resume] running stages after S3 only: crystallize + return")
        crystallize(out, kb, goal)
        print(f"[return] {out / 'return_contract.md'}")
        return 0
    if current in {"S5_CRYSTALLIZE", "S6_RETURN"}:
        print("[resume] loop already crystallized/returned; nothing to rerun.")
        print(f"[return] {out / 'return_contract.md'}")
        return 0

    print(f"Unknown current_state: {current}")
    return 1


# ---------- V0.4 commands ----------


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a V0.4 workspace: create the package-relative dirs in `path`."""
    from . import v04

    root = Path(args.path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "work" / "runs").mkdir(parents=True, exist_ok=True)
    kb = root / args.kb
    v04.ensure_kb_v04(kb)
    print(f"[init] workspace at {root}")
    print(f"[init] knowledge base: {kb} ({', '.join(v04.KB_FILES)})")
    print(f"[init] runs dir: {root / 'work' / 'runs'}")
    return 0


def cmd_v04_add(args: argparse.Namespace) -> int:
    from . import v04

    kb = Path(args.kb)
    new_sources, new_nodes = v04.ingest_sources(kb, args.inputs, args.mode)
    print(f"[done] +{new_sources} sources, +{new_nodes} nodes -> {kb / 'nodes.jsonl'}")
    return 0


def cmd_v04_map(args: argparse.Namespace) -> int:
    from . import v04

    v04.run_map(Path(args.kb), Path(args.out), args.focus, args.max_gaps)
    return 0


def cmd_v04_hypothesis(args: argparse.Namespace) -> int:
    from . import v04

    v04.run_hypothesis(Path(args.kb), Path(args.run), args.focus, args.max_per_gap)
    return 0


def cmd_v04_validate(args: argparse.Namespace) -> int:
    from . import v04

    v04.run_validate(Path(args.kb), Path(args.run))
    return 0


def cmd_v04_return(args: argparse.Namespace) -> int:
    from . import v04

    v04.run_return(Path(args.kb), Path(args.run), args.write_report, args.consolidate)
    return 0


def cmd_v04_run(args: argparse.Namespace) -> int:
    """End-to-end five-gate pipeline."""
    from . import v04

    kb = Path(args.kb)
    out = Path(args.out)
    # Gate 1 + 2
    v04.ingest_sources(kb, args.input, args.mode)
    # Gate 3
    v04.run_map(kb, out, args.focus, args.max_gaps)
    # Gate 4a
    v04.run_hypothesis(kb, out, args.focus, args.max_per_gap)
    # Gate 4b
    v04.run_validate(kb, out)
    # Gate 5
    if args.return_:
        v04.run_return(kb, out, write_report=True, consolidate=args.consolidate)
    else:
        v04.run_return(kb, out, write_report=True, consolidate=False)
    print(f"[run] complete -> {out}")
    return 0


def cmd_v04_link(args: argparse.Namespace) -> int:
    """T1: (re)build the relation layer (relations.jsonl) for a knowledge base.

    Deterministic, stdlib-only.  Stands alone so an operator can materialize or
    rebuild relations for any KB without running the full pipeline.  Relations
    are candidate literature-distillation edges, never verified causal facts.
    """
    from . import graph

    kb = Path(args.kb)
    if not kb.exists():
        print(f"[link] kb_dir does not exist: {kb}")
        return 1
    result = graph.build_relations(kb, focus=args.focus, max_gaps=args.max_gaps)
    for w in result.get("warnings", []):
        print(f"  [warn] {w}")
    print(f"[link] wrote {result['count']} relations -> {kb / graph.RELATIONS_FILE}")
    print(f"[link] gaps with >=1 relation: {sum(1 for v in result['gap_relations'].values() if v)}")
    print("[link] NOTE: relations are candidate evidence edges between distilled "
          "units, not verified causal facts (human_review_required=true).")
    return 0


def cmd_v04_validate_graph(args: argparse.Namespace) -> int:
    """Read-only structural check of the relation graph (T0 contract)."""
    from . import graph

    kb = Path(args.kb)
    report = graph.validate_graph(kb)
    summary = report.get("summary", {})
    print(f"[validate-graph] kb={kb} "
          f"nodes={summary.get('nodes', 0)} relations={summary.get('relations', 0)} "
          f"gaps={summary.get('gaps', 0)} hypotheses={summary.get('hypotheses', 0)}")
    for w in report["warnings"]:
        print(f"  [warn] {w['check']}: {w['message']}")
    for e in report["errors"]:
        print(f"  [ERROR] {e['check']}: {e['message']}")
    if report["errors"]:
        print(f"[validate-graph] FAIL: {len(report['errors'])} error(s), "
              f"{len(report['warnings'])} warning(s)")
        return 1
    print(f"[validate-graph] OK: 0 errors, {len(report['warnings'])} warning(s)")
    return 0


def cmd_v04_resume(args: argparse.Namespace) -> int:
    """Idempotent resume: pick up the V0.4 run from its loop_state."""
    from . import v04

    out = Path(args.run)
    state = v04.read_loop_state(out)
    if state is None:
        # Fall back to the v0.1 resume if this is not a v0.4 run.
        ns = argparse.Namespace(run_dir=str(out), kb=args.kb, goal="",
                                max_gaps=30, max_hypotheses=10, mock=False)
        return cmd_resume(ns)

    kb = Path(args.kb or state.get("kb_path", "knowledge_base"))
    focus = state.get("focus", "")
    current = state.get("current_state", "")
    counts = state.get("counts", {})
    max_per_gap = getattr(args, "max_per_gap", 2)
    max_gaps = getattr(args, "max_gaps", 60)
    print(f"[resume] v0.4 run={out.name} current_state={current} kb={kb}")

    # Verify required artifacts before trusting the recorded state.
    required = {
        "G3_MAP": [out / "gaps.jsonl"],
        "G4_GENERATE": [out / "gaps.jsonl", out / "hypothesis_candidates.jsonl"],
        "G4_VALIDATE": [out / "gaps.jsonl", out / "hypothesis_candidates.jsonl", out / "validations.jsonl"],
        "G5_RETURN": [out / "return_contract.md", out / "final_report.md"],
    }
    for name in required.get(current, []):
        if not name.exists() or name.stat().st_size == 0:
            print(f"[warn] artifact missing/empty: {name} (will rebuild from this stage)")
            current = "G2_FIELD"  # force re-run from map onward
            break

    order = v04.V04_STATES
    idx = order.index(current) if current in order else 0

    # Run whatever stages remain after the current completed state.
    if idx < order.index("G3_MAP"):
        v04.run_map(kb, out, focus, max_gaps)
    if idx < order.index("G4_GENERATE"):
        if not (out / "hypothesis_candidates.jsonl").exists() or current in ("G1_INPUT", "G2_FIELD"):
            v04.run_hypothesis(kb, out, focus, max_per_gap)
    if idx < order.index("G4_VALIDATE"):
        if not (out / "validations.jsonl").exists():
            v04.run_validate(kb, out)
    if idx < order.index("G5_RETURN"):
        v04.run_return(kb, out, write_report=True, consolidate=True)

    if current == "G5_RETURN":
        print("[resume] loop already returned; nothing to rerun.")
        print(f"[return] {out / 'return_contract.md'}")
    else:
        print(f"[resume] advanced run to G5_RETURN -> {out}")
    return 0


# ---------- utility/demo ----------


def cmd_init_demo(args: argparse.Namespace) -> int:
    root = Path(args.path)
    src = root / "examples" / "paper_to_hypothesis" / "sources"
    src.mkdir(parents=True, exist_ok=True)
    demo = src / "sample_paper_notes.md"
    demo.write_text(
        """# Sample Distilled Paper Notes\n\n"
        "## Findings\n"
        "- Finding: Intervention A improves marker B in a small population, but boundary conditions are unclear.\n"
        "- Method: The study uses repeated dietary records and simple regression.\n"
        "- Limitation: Long-term adherence and subgroup effects were not tested.\n\n"
        "## Open Questions\n"
        "- Open question: whether the same mechanism works in clinical nutrition RAG systems.\n"
        "- Gotcha: summary-only evidence should not be treated as full-text verification.\n"
        """,
        encoding="utf-8",
    )
    print(f"[demo] wrote {demo}")
    return 0


# ---------- argparse ----------


def _dispatch_add(args: argparse.Namespace) -> int:
    """`add` dispatches to V0.4 ingest when --mode is given, else v0.1 Markdown add."""
    if getattr(args, "mode", None):
        return cmd_v04_add(args)
    return cmd_add(args)


def _dispatch_map(args: argparse.Namespace) -> int:
    """`map` dispatches to V0.4 when --focus is given, else v0.1 heuristic map."""
    if getattr(args, "focus", None):
        return cmd_v04_map(args)
    return cmd_map(args)


def _dispatch_resume(args: argparse.Namespace) -> int:
    """`resume` dispatches to V0.4 when --run is given, else v0.1 positional resume."""
    if getattr(args, "run", None):
        return cmd_v04_resume(args)
    if getattr(args, "run_dir", None):
        return cmd_resume(args)
    raise SystemExit("resume needs a run directory: positional <run_dir> (v0.1) or --run (v0.4)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shengwanwu-loop", description="Shengwanwu Loop hypothesis runner (v0.1 + v0.4)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- V0.4 init ----
    ini = sub.add_parser("init", help="Initialize a V0.4 workspace (kb + runs dir)")
    ini.add_argument("path", nargs="?", default=".")
    ini.add_argument("--kb", default="knowledge_base_v04")
    ini.set_defaults(func=cmd_init)

    # ---- add (v0.1 Markdown OR v0.4 distilled via --mode) ----
    add = sub.add_parser("add", help="Add distilled sources into a persistent knowledge base")
    add.add_argument("inputs", nargs="+", help="Markdown/JSONL file(s) or directories")
    add.add_argument("--kb", default="knowledge_base", help="knowledge base directory")
    add.add_argument("--mode", choices=["distilled-jsonl", "markdown", "auto"], default=None,
                     help="V0.4 ingest mode; if set, uses the V0.4 distilled-source pipeline")
    add.add_argument("--mock", action="store_true", help="allow mock nodes if input is sparse (v0.1)")
    add.set_defaults(func=_dispatch_add)

    # ---- map (v0.1 OR v0.4 via --focus) ----
    mp = sub.add_parser("map", help="Build a gap map from the knowledge base")
    mp.add_argument("--kb", default="knowledge_base")
    mp.add_argument("--out", default="", help="run directory for gap snapshot")
    mp.add_argument("--focus", default=None, help="V0.4 focus string; if set, uses V0.4 gap operators")
    mp.add_argument("--max-gaps", type=int, default=60)
    mp.add_argument("--mock", action="store_true")
    mp.set_defaults(func=_dispatch_map)

    # ---- V0.4 hypothesis ----
    h4 = sub.add_parser("hypothesis", help="V0.4: generate hypothesis candidates from gaps")
    h4.add_argument("--kb", default="knowledge_base_v04")
    h4.add_argument("--run", required=True, help="run directory holding gaps.jsonl")
    h4.add_argument("--focus", default="")
    h4.add_argument("--max-per-gap", type=int, default=2)
    h4.set_defaults(func=cmd_v04_hypothesis)

    # ---- V0.4 validate (Six-Eyes) ----
    v4 = sub.add_parser("validate", help="V0.4: Six-Eyes validation of hypothesis candidates")
    v4.add_argument("--kb", default="knowledge_base_v04")
    v4.add_argument("--run", required=True)
    v4.set_defaults(func=cmd_v04_validate)

    # ---- V0.4 return ----
    r4 = sub.add_parser("return", help="V0.4: write report + return contract, optional consolidate")
    r4.add_argument("--kb", default="knowledge_base_v04")
    r4.add_argument("--run", required=True)
    r4.add_argument("--write-report", action="store_true")
    r4.add_argument("--consolidate", action="store_true")
    r4.set_defaults(func=cmd_v04_return)

    # ---- V0.4 link (T1: build/rebuild relations.jsonl) ----
    lk = sub.add_parser("link",
                        help="V0.4 T1: build/rebuild the relation layer (relations.jsonl) for a KB")
    lk.add_argument("--kb", default="knowledge_base_v04")
    lk.add_argument("--focus", default="")
    lk.add_argument("--max-gaps", type=int, default=200,
                    help="cap on gaps used to derive relations (default 200)")
    lk.set_defaults(func=cmd_v04_link)

    # ---- V0.4 validate-graph (read-only relation-graph contract check) ----
    vg = sub.add_parser("validate-graph",
                        help="V0.4: read-only structural check of the relation graph (T0)")
    vg.add_argument("--kb", default="knowledge_base_v04")
    vg.set_defaults(func=cmd_v04_validate_graph)

    # ---- V0.4 run (end-to-end) ----
    rn = sub.add_parser("run", help="V0.4: end-to-end five-gate pipeline")
    rn.add_argument("--input", nargs="+", required=True, help="distilled source file(s)")
    rn.add_argument("--kb", default="knowledge_base_v04")
    rn.add_argument("--out", required=True, help="run directory")
    rn.add_argument("--mode", choices=["distilled-jsonl", "markdown", "auto"], default="distilled-jsonl")
    rn.add_argument("--focus", default="")
    rn.add_argument("--max-gaps", type=int, default=60)
    rn.add_argument("--max-per-gap", type=int, default=2)
    rn.add_argument("--return", dest="return_", action="store_true", help="run the return gate")
    rn.add_argument("--consolidate", action="store_true")
    rn.set_defaults(func=cmd_v04_run)

    gen = sub.add_parser("generate", help="Generate hypotheses from gap_map; auto-map if needed")
    gen.add_argument("--goal", required=True)
    gen.add_argument("--kb", default="knowledge_base")
    gen.add_argument("--out", required=True)
    gen.add_argument("--max-gaps", type=int, default=30)
    gen.add_argument("--max-hypotheses", type=int, default=10)
    gen.add_argument("--remap", action="store_true", help="force rebuilding gap_map before generating")
    gen.add_argument("--mock", action="store_true")
    gen.set_defaults(func=cmd_generate)

    hyp = sub.add_parser("hypothesize", help="Alias for generate")
    hyp.add_argument("--goal", required=True)
    hyp.add_argument("--kb", default="knowledge_base")
    hyp.add_argument("--out", required=True)
    hyp.add_argument("--max-gaps", type=int, default=30)
    hyp.add_argument("--max-hypotheses", type=int, default=10)
    hyp.add_argument("--remap", action="store_true")
    hyp.add_argument("--mock", action="store_true")
    hyp.set_defaults(func=cmd_generate)

    res = sub.add_parser("resume", help="Resume from loop_state.json (v0.1 positional or v0.4 --run)")
    res.add_argument("run_dir", nargs="?", default=None, help="v0.1 run directory (positional)")
    res.add_argument("--run", default=None, help="v0.4 run directory")
    res.add_argument("--kb", default="")
    res.add_argument("--goal", default="")
    res.add_argument("--max-gaps", type=int, default=60)
    res.add_argument("--max-per-gap", type=int, default=2)
    res.add_argument("--max-hypotheses", type=int, default=10)
    res.add_argument("--mock", action="store_true")
    res.set_defaults(func=_dispatch_resume)

    demo = sub.add_parser("init-demo", help="Create a demo source file under a target project directory")
    demo.add_argument("path", nargs="?", default=".")
    demo.set_defaults(func=cmd_init_demo)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
