import React, { useEffect, useState } from "react";

const API_BASE = "http://localhost:8000";
const KB_PATH = "knowledge_base";

export default function ShengwanwuApp() {
  const [goal, setGoal] = useState("测试假说生发");
  const [notes, setNotes] = useState("# 测试笔记\n\n这是一篇关于营养学的笔记。");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [stats, setStats] = useState({ total_nodes: 0, total_hypotheses: 0, total_gaps: 0, total_lineage: 0, total_mother_patches: 0, total_review_states: 0, sources: [] });
  const [gaps, setGaps] = useState([]);
  const [hypotheses, setHypotheses] = useState([]);
  const [lineage, setLineage] = useState([]);
  const [motherPatches, setMotherPatches] = useState([]);
  const [reviewStates, setReviewStates] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState([]);

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) {
      const message = data?.detail?.error || data?.error || JSON.stringify(data);
      throw new Error(message);
    }
    return data;
  }

  async function refreshStats() {
    const data = await fetchJson(`${API_BASE}/api/kb/stats?kb_path=${encodeURIComponent(KB_PATH)}`);
    setStats(data);
  }

  async function runLoop() {
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson(`${API_BASE}/api/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal, notes, kb_path: KB_PATH }),
      });
      setGaps(data.gaps || []);
      setHypotheses(data.hypotheses || []);
      setLineage(data.lineage || []);
      setMotherPatches(data.mother_patches || []);
      setReviewStates(data.review_states || []);
      setStats(data.kb_stats || stats);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadHistory() {
    setError("");
    try {
      const data = await fetchJson(`${API_BASE}/api/kb/hypotheses?kb_path=${encodeURIComponent(KB_PATH)}&limit=20`);
      setHistory(data || []);
      setHistoryOpen(true);
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  useEffect(() => {
    refreshStats().catch((err) => setError(err.message || String(err)));
  }, []);

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <h1>生万物 Loop</h1>
      <p style={{ color: "#666" }}>前端只调用本地 FastAPI；Claude / 五门内核 / JSONL 知识库由后端负责。</p>

      <section style={{ padding: 16, border: "1px solid #ddd", borderRadius: 12, marginBottom: 16 }}>
        <strong>知识库：</strong>
        {stats.total_nodes} 节点 · {stats.total_hypotheses} 条历史假说 · {stats.total_gaps} 个缺口 · {stats.total_lineage || 0} 条血脉 · {stats.total_mother_patches || 0} 个母体补丁 · {stats.total_review_states || 0} 条审稿状态
        <button onClick={refreshStats} style={{ marginLeft: 12 }}>刷新统计</button>
        <button onClick={loadHistory} style={{ marginLeft: 8 }}>历史假说</button>
      </section>

      <label style={{ display: "block", marginBottom: 12 }}>
        研究目标
        <input value={goal} onChange={(e) => setGoal(e.target.value)} style={{ width: "100%", padding: 10, marginTop: 6 }} />
      </label>

      <label style={{ display: "block", marginBottom: 12 }}>
        蒸馏笔记（Markdown）
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={12} style={{ width: "100%", padding: 10, marginTop: 6 }} />
      </label>

      <button onClick={runLoop} disabled={loading} style={{ padding: "10px 18px", borderRadius: 8 }}>
        {loading ? "生发中..." : "运行生万物 Loop"}
      </button>

      {error && <pre style={{ color: "#b00020", whiteSpace: "pre-wrap" }}>{error}</pre>}

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 24 }}>
        <div>
          <h2>本次缺口</h2>
          {gaps.map((gap) => <GapCard key={gap.gap_id} gap={gap} />)}
        </div>
        <div>
          <h2>本次假说</h2>
          {hypotheses.map((hyp) => <HypothesisCard key={hyp.hypothesis_id} hyp={hyp} />)}
        </div>
      </section>

      <section style={{ padding: 16, border: "1px solid #ddd", borderRadius: 12, marginTop: 16 }}>
        <h2>本次三根骨</h2>
        <p>血脉 lineage：{lineage.length} 条 · 母体补丁 mother_patch：{motherPatches.length} 个 · 审稿状态 review_state：{reviewStates.length} 条</p>
        <p style={{ color: "#666" }}>mother_patch 只是待审补丁，不自动改写母体；支持 GUI / TUI 后续展示血脉、差异与人工升格。</p>
      </section>

      {historyOpen && (
        <section style={{ marginTop: 24 }}>
          <h2>历史假说</h2>
          {history.map((hyp) => <HypothesisCard key={hyp.hypothesis_id} hyp={hyp} />)}
        </section>
      )}
    </main>
  );
}

function GapCard({ gap }) {
  return (
    <article style={cardStyle}>
      <strong>{gap.gap_type}</strong>
      <p>{gap.description}</p>
      <small>priority: {gap.priority_score} · {gap.gap_id}</small>
    </article>
  );
}

function HypothesisCard({ hyp }) {
  return (
    <article style={cardStyle}>
      <strong>{hyp.validation_status || "candidate"}</strong>
      <p>{hyp.claim}</p>
      <small>{hyp.reasoning_type} · confidence {hyp.confidence} · {hyp.hypothesis_id}</small>
    </article>
  );
}

const cardStyle = {
  padding: 14,
  border: "1px solid #e0e0e0",
  borderRadius: 12,
  marginBottom: 12,
  background: "#fffdf8",
};
