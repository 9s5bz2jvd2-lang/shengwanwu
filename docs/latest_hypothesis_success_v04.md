# V0.4 最新假说成功｜金星行星科学重跑

> 目的：展示《生万物》不仅能从论文库生成候选假说，还能保留假说血脉、提出母体补丁、记录审稿状态。

## 本轮输入

- 主题：金星云层与行星科学
- 来源：6 篇已蒸馏金星论文
- 知识节点：175 个蒸馏节点
- 运行版本：V0.4 三根骨版

## 本轮结果

| 项目 | 数量 |
|---|---:|
| gaps | 80 |
| hypotheses | 94 |
| validations | 94 |
| supported / pass | 28 |
| needs_review / contested | 66 |
| lineage | 94 |
| mother_patch | 94 |
| review_state | 94 |

## 代表性行星科学缺口

> The coupling between radiative balance, UV/IR signatures, and cloud microphysics is under-specified.

## 代表性 supported candidate

> A planetary-scale coupling across the domains named in gap may explain the Venus cloud behavior better than treating each domain separately.

另一个候选方向：

> The apparent signal may be controlled by a scale mismatch: local chemistry or microphysics is being interpreted as a planet-wide atmospheric pattern.

## 论文已报告事实与候选假说的边界

本轮整理出 9 条较硬的论文事实，例如：

1. 金星云层主成分为浓硫酸 H2SO4，约 75%–98%。
2. 上层云为光化学云，SO2 与 H2O 光化学生成 H2SO4 后凝结。
3. 云顶 H2SO4 光化学循环中，约 86% 向上再凝结，约 14% 向下热分解。
4. 云层温压约 300–350 K、0.1–1 bar，但水活度远低于已知极端微生物可承受范围。
5. 云酸度观测有显著不确定性。
6. Lefèvre 等小尺度湍流模型给云层 Kzz 约 10^6–10^8 cm²/s。
7. 未知 UV 吸收体与 0.32–0.5 μm 低反照率、云纹和云顶加热相关。
8. UV 吸收体主要位于 >57 km 上云区，365 nm 反照率有强年代变化。
9. 20 种生物氨基酸中 19 种骨架在浓硫酸中保持完整；色氨酸完全降解。

**边界：**这些事实可称“论文已报告 / 已观测 / 已有实验支持”；《生万物》生成的 94 条仍是候选链，不可称为已证实结论。

## 三根骨

- `lineage.jsonl`：每条假说的 source → node → gap → hypothesis 血脉。
- `mother_patch.jsonl`：若假说成立，应如何修改母体世界模型；只是待审补丁，不自动改母体。
- `review_state.jsonl`：candidate / contested / reviewed / supported / rejected 阶梯。

完整 HTML 展示见：[`docs/venus_planetary_science_v04_spine.html`](venus_planetary_science_v04_spine.html)。
