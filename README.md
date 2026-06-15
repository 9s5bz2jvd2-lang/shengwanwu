# 生万物论 (Shēng Wàn Wù)

> **道生一 · 一生二 · 二生三 · 三生万物**
>
> 从学术论文库到可行动研究假说的自动发现引擎

**作者：王润圆** · 昆明医科大学 · 营养与食品卫生学硕士

---

## 这是什么？

《生万物论》是一个轻量级研究工具，能从一批学术论文中自动：
1. 蒸馏知识节点（方法、发现、概念、局限）
2. 构建跨论文知识图谱
3. 检测知识空白与矛盾
4. 生成可证伪的研究假说
5. 排序输出 Top-N 研究问题

## 快速开始

### 环境要求
- Python 3.10+
- 需要的包：`pip install trafilatura pymupdf`

### 运行流程

```bash
# 第一步：准备论文库
# 将你的论文放入 papers/ 目录（支持 PDF 和 HTML）

# 第二步：蒸馏知识节点
python shengwanwu/distill.py --input papers/ --output library/

# 第三步：构建知识图谱（自动）
python shengwanwu/hypothesis_scan_v02.py --library library/

# 第四步：生成研究假说
python shengwanwu/reconstruct_v03.py --library library/

# 第五步：收集排序
python shengwanwu/collect_rank.py --library library/
```

### 示例数据

本仓库包含两个已跑通的示例：

- `examples/venus_cloud/` — 金星云层生命研究（6篇论文 → 175节点 → 96假说）
- `examples/world_models/` — 世界模型研究（25篇论文 → 231节点 → 107假说）

## 四层架构

```
道（原始论文）
  ↓ 蒸馏
一（知识节点：方法/发现/概念/局限）
  ↓ 图谱
二（跨论文知识网络：6种模式检测）
  ↓ 重构
三（语义假说：可证伪+实验方案）
  ↓ 排序
万物（Top-N 可行动研究问题）
```

## 核心脚本说明

| 脚本 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `hypothesis_scan_v02.py` | 知识图谱+假说检测 | node_store + graph_edges | hypothesis_candidates.json |
| `reconstruct_v03.py` | LLM语义重构（三层架构） | 候选假说 + 知识上下文 | 结构化研究问题 |
| `physics_hypotheses.py` | 物理导向假说生成 | 局限/不确定性节点 | 物理假说 |
| `collect_rank.py` | 去重+grounding+排序 | 所有假说结果 | ranked_results.json |

## 设计原则

1. **由库成论**：一切假说从蒸馏的论文全文中生成，可追溯
2. **六眼旁观**：六种模式检测器多角度审视知识网络
3. **可证伪**：每条假说包含明确的证伪条件
4. **低功耗**：确定性工作交脚本，判断性工作交LLM

## 技术栈

- 论文获取：curl + trafilatura + PyMuPDF
- 知识蒸馏：并行分神（daemon）处理，DeepSeek/Claude Code
- 知识图谱：Python 原生，共享标签→边连接
- 假说重构：类型特定 prompt 模板 + LLM 语义生成
- 运行于灵台（LingTai）生态

## 许可

© 王润圆 2026. 保留所有权利。

---

*Powered by 灵台 LingTai*
