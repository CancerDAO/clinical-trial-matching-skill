---
name: clinical-trial-matching-skill
description: Trial shortlist with dual-source search (ClinicalTrials.gov + ChiCTR)
keywords:
  - retrieval
  - ranking
  - ClinicalTrials
  - ChiCTR
  - patient-profile
  - clinical-trial-matching
  - 中国临床试验
measurable_outcome: Produce ≥5 matched trials (when available) with rationale + missing-data notes within 3 minutes of receiving a patient query.
license: MIT
metadata:
  author: CancerDAO
  inspired_by: NCBI TrialGPT (ncbi-nlp/TrialGPT)
  version: "1.5.0"
compatibility:
  - system: Python 3.9+
  - external: chictr-mcp-server (required for ChiCTR search)
allowed-tools:
  - run_shell_command
  - read_file
  - chictr_search_trials
  - chictr_get_trial_detail
---

# TrialGPT Matching (VitaClaw Enhanced)

Run the TrialGPT pipeline to retrieve, match, and explain candidate trials for a patient before deeper eligibility review.
Enhanced with **dual-source search** to cover both ClinicalTrials.gov and ChiCTR (中国临床试验注册中心).

## 数据源说明

| 数据源 | 覆盖范围 | 查询方式 |
|--------|----------|----------|
| ClinicalTrials.gov | 全球临床试验 | TrialGPT Retriever + ClinicalTrials.gov API |
| ChiCTR (中国临床试验注册中心) | 中国注册临床试验 | chictr-mcp-server |

> **为什么需要两个数据源？**
> ClinicalTrials.gov 上中国的临床试验数据可能不全。ChiCTR 补充了在中国注册、尚未同步到 ClinicalTrials.gov 的试验。

## Inputs

- Patient summary (structured JSON or free text) with condition keywords.
- **核心诉求为可选项** — 大部分患者并不清楚自己的核心诉求，skill 应在无核心诉求输入时，基于诊断、分子特征、治疗史自动推断匹配方向。
- Optional filters: geography, phase, intervention, biomarker.
- 数据源偏好: `NCT` / `ChiCTR` / `both` (默认: both)

## Outputs

最终输出为一份 **独立的 HTML 文件**，无外部依赖，可直接在浏览器中打开查看。

HTML 报告模板位于: `repo/report/template.html`

### HTML 报告结构 (8 个板块)

```
┌─ header ───────────────────────────────────────────┐
│ 临床试验匹配报告 | 报告日期 | 数据源 | 适用场景     │
├─ 1. 患者画像摘要 ──────────────────────────────────┤
│ 4列 grid 卡片: 癌种, 分期, 分子特征(mol-tag),      │
│ 当前治疗, 治疗线数, 器官功能, 用药禁忌, 基本信息    │
├─ 2. 治疗决策史 (timeline) ─────────────────────────┤
│ 时间线: 每条治疗 → 疗效 badge (PR/SD/PD/毒性)      │
│ 样式: .tl-item / .tl-fail / .tl-active             │
├─ 3. 系统匹配结论 ─────────────────────────────────┤
│ 一段话: 检索策略统计 + 匹配结果总数                  │
├─ 4. 候选临床试验清单 ─────────────────────────────┤
│ 4a. 高度匹配 (table): 等级 | ID(可点击) | 试验方向  │
│     | 阶段 | 匹配理由 | 入组注意事项 | 中国中心      │
│ 4b. 条件匹配 (table): 同上                         │
├─ 5. 逐条入排标准评估 (NEW) ───────────────────────┤
│ 可折叠卡片 (.criteria-card), 每试验一张             │
│ 入选标准: 逐条 ✅符合/❌不符合/⚠️边界/❓缺失        │
│ 排除标准: 逐条 ✅无冲突/❌触发排除/⚠️可能冲突/❓缺失 │
│ 每条含 判定 + 依据 (与患者哪项数据比对得出)          │
├─ 6. 已排除方向及理由 ─────────────────────────────┤
│ .excluded-item 卡片: 试验名 + 排除原因              │
├─ 7. 临床中心信息汇总 ─────────────────────────────┤
│ 3列 grid .referral-card: 医院 + 匹配试验 + 优势    │
│ 说明 SMO vs 自行联系路径                            │
├─ 8. 信息缺口提示 + 行动清单 ──────────────────────┤
│ .gap-item: 紧急/重要/建议 分级                      │
│ .todo-list: checkbox + 标题 + 描述 + 责任人         │
├─ 声明 + footer ───────────────────────────────────┤
│ 免责声明 + 检索统计 + 校验摘要 + 版本号             │
└────────────────────────────────────────────────────┘
```

### HTML 组件速查 (LLM 生成报告时参照)

| 组件 | CSS class | 用途 |
|------|-----------|------|
| 分子标签 | `.mol-tag` | 关键突变 (绿色) |
| 分子标签-警告 | `.mol-tag.mol-tag-warn` | 不利因素 (橙色) |
| 分子标签-中性 | `.mol-tag.mol-tag-muted` | 中性信息 (灰色) |
| 疗效-有效 | `.badge.badge-good` | PR / SD / R0 |
| 疗效-进展 | `.badge.badge-danger` | PD / 毒性停药 |
| 匹配-高 | `.badge.badge-good` | 高匹配试验 |
| 匹配-条件 | `.badge.badge-warn` | 条件匹配试验 |
| 匹配-方向 | `.badge.badge-info` | ATM靶向 / 细胞治疗 等 |
| 试验ID链接 | `a.badge-nct` | NCT/ChiCTR 可点击 |
| 缺口-紧急 | `.badge.badge-danger` | 紧急缺失数据 |
| 缺口-重要 | `.badge.badge-warn` | 重要缺失数据 |
| 缺口-建议 | `.badge.badge-info` | 建议补充 |
| 时间线-正常 | `.tl-item` | 正常治疗节点 |
| 时间线-失败 | `.tl-item.tl-fail` | PD / 毒性 |
| 时间线-当前 | `.tl-item.tl-active` | 当前方案 |
| 标准评估卡 | `.criteria-card` | 可折叠的逐条评估卡片 |
| 标准-符合 | `.crit-met` | ✅ 符合 / 无冲突 |
| 标准-不符 | `.crit-fail` | ❌ 不符合 / 触发排除 |
| 标准-边界 | `.crit-warn` | ⚠️ 边界 / 可能冲突 |
| 标准-缺失 | `.crit-unknown` | ❓ 信息缺失 |
| 排除卡片 | `.excluded-item` | 被排除的试验 |
| 转诊卡片 | `.referral-card` | 临床中心 |
| 行动项 | `.todo-list li` | 下一步行动 |

### NCT/ChiCTR 链接格式

```html
<!-- NCT -->
<a href="https://clinicaltrials.gov/study/NCTxxxxxxxx" target="_blank" class="badge-nct">NCTxxxxxxxx</a>
<!-- ChiCTR -->
<a href="https://www.chictr.org.cn/showproj.html?proj=ChiCTRxxxxxxxx" target="_blank" class="badge-nct">ChiCTRxxxxxxxx</a>
```

### 对外文档合规要求

- **不输出评分/打分** — 数字评分可能被解读为治疗建议，对外文档中不展示评分和优先级星级。内部调试可保留。
- **不使用"推荐"一词** — 列名使用"匹配理由"而非"推荐理由"，避免构成临床建议。
- **不输出"优先推荐行动"章节** — 不做治疗推荐。可输出"临床中心信息"供患者参考就医路径。
- **提供研究者信息** — 如患者需自行联系，应提供研究者姓名、医院、科室信息。

## 工作流 (Enhanced v1.2) — LLM + 脚本协作

本 skill 采用 **LLM 负责临床推理 + 脚本负责并行执行** 的协作模式:

- **LLM 做什么**: 读取病历 → 提取患者画像 → 生成结构化检索计划 (search plan JSON) → 分析匹配结果 → 撰写报告
- **脚本做什么**: 接收关键词 JSON → 并行查询 ClinicalTrials.gov API + ChiCTR → 去重 → 硬筛选 → 返回结构化结果

```
Step 0: 前置依赖检查
    │
    ├─ 检测 chictr-mcp-server 是否可用
    └─ 不可用则提示安装，或仅使用 ClinicalTrials.gov
    │
Step 1: 患者画像提取 [LLM]
    │
    ├─ read_file → 患者摘要
    ├─ 提取: 诊断、分子特征、治疗线数、合并症、禁忌
    ├─ **治疗线数**: 明确标注患者已完成的治疗线数 (如: 5线)
    │   作为硬筛选条件 — 仅限一线的试验对多线患者直接排除
    ├─ **ECOG**: 默认视为符合条件，不作为严格排除指标
    │   (临床实践中能活动的患者一般都满足，且病历中常不记录ECOG)
    └─ 核心诉求: 可选，无则自动推断
    │
Step 2: 生成检索计划 [LLM → JSON]
    │
    ├─ LLM 基于患者画像, 生成 search_plan.json:
    │   {
    │     "patient_summary": "...",
    │     "treatment_lines": 5,
    │     "keyword_groups": [
    │       {"label": "疾病+突变特异", "source": "both",
    │        "queries": [{"condition": "colorectal cancer", "term": "KRAS G12C"}]},
    │       {"label": "泛化-实体瘤", "source": "nct",
    │        "queries": [{"condition": "solid tumor", "term": "KRAS G12C"}]},
    │       {"label": "联合靶点", "source": "both",
    │        "queries": [{"condition": null, "term": "KRAS G12C SHP2 inhibitor"}]},
    │       {"label": "细胞治疗(不限突变)", "source": "both",
    │        "queries": [
    │          {"condition": "colorectal cancer", "term": "CAR-T"},
    │          {"condition": "solid tumor", "term": "TIL"},
    │          {"condition": null, "term": "CEA CAR-T"}
    │        ]},
    │       {"label": "中文关键词", "source": "chictr",
    │        "queries": [{"condition": null, "term": "结直肠癌 KRAS"}]}
    │     ],
    │     "hard_exclude": {
    │       "first_line_only": true,
    │       "molecular_mismatch": ["RAS wild-type only"]
    │     }
    │   }
    │
    ├─ 关键词策略 (LLM 应遵循, 8 个维度缺一不可):
    │   ├─ 1. 疾病特异关键词 (如: colorectal cancer KRAS G12C)
    │   ├─ 2. 泛化关键词 "实体瘤/solid tumor" (避免遗漏)
    │   ├─ 3. 细胞治疗关键词不限定突变 (如: CAR-T colorectal, TIL 实体瘤)
    │   ├─ 4. 联合靶点关键词 (如: SHP2 + KRAS)
    │   ├─ 5. 通路靶向关键词 (如: ATM PARP, 基于患者特有突变)
    │   ├─ 6. 中文关键词 (用于 ChiCTR 查询)
    │   ├─ ⚠️ 7. **具体药物名穷举** — 列出该靶点所有已知在研药物,逐个搜索:
    │   │       例: KRAS G12C 领域应搜索: sotorasib, adagrasib, glecirasib,
    │   │       divarasib, fulzerasib, olomorasib, garsorasib, D3S-001,
    │   │       calderasib, PF-07934040, BGB-53038 等具体药物名
    │   │       + 每个药物名与瘤种组合 (如: "glecirasib cetuximab colorectal")
    │   └─ ⚠️ 8. **耐药后策略关键词** — 基于患者当前用药,搜索耐药后的下一步:
    │           例: 患者正在用 GDP-bound KRAS G12C 抑制剂(氟泽雷赛),
    │           应搜索: "RAS-ON inhibitor", "active-state KRAS",
    │           "KRAS G12C resistance", "KRAS inhibitor rechallenge"
    │           → 这是临床逻辑: 当前药物失效后最可能有效的方向
    │
Step 3: 并行查询 + 硬筛选 [脚本]
    │
    ├─ 运行: python dual_source_search.py --plan search_plan.json --out results.json
    │
    ├─ 脚本内部流程:
    │   ├─ 读取 search_plan.json
    │   ├─ 并行查询 ClinicalTrials.gov API (ThreadPoolExecutor, 5 workers)
    │   ├─ 合并 ChiCTR 结果 (由 LLM 通过 MCP tool 查询后传入)
    │   ├─ 按 trial ID 去重
    │   ├─ 硬排除: 一线试验 / 分子特征不匹配
    │   └─ 输出 results.json:
    │       {
    │         "included_trials": [...],    // 通过筛选的试验
    │         "excluded_trials": [...],    // 被排除的试验 (含原因)
    │         "search_stats": {...}        // 检索统计
    │       }
    │
    ├─ 脚本自动提取的结构化字段:
    │   ├─ trial ID, 标题, 期别, 申办方
    │   ├─ 治疗线数判断 (first_line / 2L+)
    │   ├─ 既往 KRAS 抑制剂排除标志
    │   ├─ 中国临床中心列表 (医院, 城市, 研究者联系人)
    │   └─ 入组标准摘要
    │
Step 4: 匹配分析 + 逐条标准评估 + 分级 [LLM]
    │
    ├─ 4a. 粗筛: LLM 读取 results.json, 按匹配度筛选 top 15-20 候选试验
    │
    ├─ 4b. ⚠️ 逐条入排标准评估 (Criterion-level Assessment)
    │   │
    │   │   借鉴 TrialMatchAI 的 Chain-of-Thought 逐条评估方法:
    │   │   对 top 候选试验, 读取 parsed_criteria (入选/排除标准列表),
    │   │   逐条与患者画像比对, 输出结构化评估:
    │   │
    │   │   入选标准评估:
    │   │   ┌──────────────────────────────────┬──────────┬─────────────────┐
    │   │   │ 标准                              │ 判定     │ 依据            │
    │   │   ├──────────────────────────────────┼──────────┼─────────────────┤
    │   │   │ KRAS G12C mutation confirmed      │ ✅ 符合  │ 患者 G12C 11.5% │
    │   │   │ ECOG PS 0-1                       │ ✅ 符合  │ ECOG 1          │
    │   │   │ eGFR ≥ 60 mL/min                 │ ⚠️ 边界  │ 患者 eGFR 58.93 │
    │   │   │ Measurable disease per RECIST     │ ❓ 未知  │ 缺少最新影像     │
    │   │   └──────────────────────────────────┴──────────┴─────────────────┘
    │   │
    │   │   排除标准评估:
    │   │   ┌──────────────────────────────────┬──────────┬─────────────────┐
    │   │   │ 标准                              │ 判定     │ 依据            │
    │   │   ├──────────────────────────────────┼──────────┼─────────────────┤
    │   │   │ Prior KRAS G12C inhibitor         │ ⚠️ 冲突  │ 正在用氟泽雷赛  │
    │   │   │ Active brain metastases           │ ✅ 无冲突│ 无脑转移记录     │
    │   │   │ Uncontrolled diabetes             │ ✅ 无冲突│ 糖尿病已控制     │
    │   │   └──────────────────────────────────┴──────────┴─────────────────┘
    │   │
    │   │   判定类别:
    │   │   - 入选标准: ✅ 符合 / ❌ 不符合 / ⚠️ 边界 / ❓ 信息缺失
    │   │   - 排除标准: ✅ 无冲突 / ❌ 触发排除 / ⚠️ 可能冲突 / ❓ 信息缺失
    │   │
    │   └─ 脚本已提供 parsed_criteria 字段 (inclusion[] + exclusion[] 列表)
    │       LLM 仅需逐条比对, 不需要自行解析原文
    │
    ├─ 4c. 生成匹配理由 (注意: 不使用"推荐"一词)
    ├─ 标注 missing data (从 ❓ 信息缺失项汇总)
    ├─ **获取临床中心信息** (医院、城市、研究者姓名)
    │
    ├─ 4d. ⚠️ 匹配等级分级规则 (硬规则, 不可自由裁量):
    │
    │   以下任一条件为真 → 该试验 **不得** 标为"高度匹配", 必须降为"条件匹配":
    │
    │   ├─ R1: 试验排除既往使用过同类药物, 且患者正在用或用过该类药物
    │   │       例: 试验排除既往 KRAS G12C 抑制剂 + 患者正在用氟泽雷赛 → 条件匹配
    │   │
    │   ├─ R2: 试验限定的治疗线数 < 患者已完成的治疗线数
    │   │       例: 试验限 2-3L + 患者已 5L+ → 条件匹配 (如果是硬限 1L → 直接排除)
    │   │
    │   ├─ R3: 试验的优先扩展瘤种不含患者的瘤种
    │   │       例: 试验主要招 BRCA 乳腺癌/卵巢癌, 结肠癌仅理论上合格 → 条件匹配
    │   │
    │   ├─ R4: 试验要求的器官功能指标, 患者在边界或不达标
    │   │       例: 试验要求 eGFR ≥ 60, 患者 eGFR 58.93 → 条件匹配
    │   │
    │   └─ R5: 逐条评估中存在 ≥2 个 ❓ 信息缺失项 (关键标准)
    │           → 条件匹配, 并在报告中列出需补充的信息
    │
    │   "高度匹配" 仅限于: 无上述任何冲突, 且瘤种/突变/线数全部明确匹配的试验
    │
Step 5: 生成 HTML 报告 [LLM]
    │
    ├─ 读取模板: repo/report/template.html
    ├─ 用匹配分析结果填充 7 个板块:
    │   ├─ 患者画像摘要 (grid 卡片 + mol-tag)
    │   ├─ 治疗决策史 (timeline)
    │   ├─ 系统匹配结论 (summary)
    │   ├─ 候选试验清单: 高度匹配 + 条件匹配 (table)
    │   ├─ 已排除方向 (excluded-item)
    │   ├─ 临床中心信息 (referral-card)
    │   └─ 信息缺口 + 行动清单 (gap-item + todo-list)
    ├─ 输出为独立 HTML 文件 (无外部依赖)
    └─ 保存到用户指定路径 (默认 ~/Downloads/)
    │
Step 6: 报告校验 [脚本 + LLM] ⚠️ 必须执行
    │
    ├─ 6a. 临床试验准确性校验 [脚本]
    │   ├─ 对报告中出现的每个 NCT/ChiCTR ID, 调用官方 API 验证:
    │   │   ├─ NCT: GET https://clinicaltrials.gov/api/v2/studies/{nctId}
    │   │   │   → 返回 404 说明 ID 错误, 标记为 ❌ INVALID
    │   │   └─ ChiCTR: chictr_get_trial_detail(registration_number)
    │   │       → 返回空说明 ID 错误, 标记为 ❌ INVALID
    │   ├─ 对每个有效 ID, 校验报告中的关键字段与 API 返回是否一致:
    │   │   ├─ 试验标题 — 是否匹配 (允许翻译/缩写差异)
    │   │   ├─ 申办方 — 是否正确
    │   │   ├─ Phase — 是否正确
    │   │   ├─ 招募状态 — 是否仍为 RECRUITING
    │   │   └─ 中国临床中心 — 数量和名称是否准确
    │   └─ 输出校验报告: 每个试验 ✅ PASS / ⚠️ MISMATCH / ❌ INVALID
    │
    ├─ 6b. 临床试验完整性校验 [LLM]
    │   ├─ 回顾 Step 2 的检索计划, 检查是否有遗漏的方向:
    │   │   ├─ 患者的每个可用靶点是否都有对应的检索词组?
    │   │   ├─ 泛化关键词 (实体瘤) 是否已使用?
    │   │   ├─ 细胞治疗是否已用不限突变的关键词检索?
    │   │   └─ 是否有已知的重要试验 (如专家提到的) 未出现在结果中?
    │   ├─ 补充检索: 如发现遗漏方向, 追加查询并合并结果
    │   └─ 输出: 覆盖度评估 (已覆盖方向 / 补充方向 / 无法覆盖原因)
    │
    ├─ 6c. 患者匹配度校验 [LLM]
    │   ├─ 对每个入选试验, 逐条审核与患者的匹配:
    │   │   ├─ 治疗线数: 试验允许的线数 vs 患者已完成的线数
    │   │   ├─ 分子特征: 试验要求的突变 vs 患者的突变
    │   │   ├─ 既往用药: 试验排除条款 vs 患者已用过的药物
    │   │   ├─ 器官功能: 试验要求 (如 eGFR, LVEF) vs 患者状况
    │   │   └─ 合并症: 试验排除的合并症 vs 患者合并症
    │   ├─ 对每个试验标注匹配结论:
    │   │   ├─ ✅ 符合 — 无明显冲突
    │   │   ├─ ⚠️ 需确认 — 有不确定条款需联系研究中心
    │   │   └─ ❌ 冲突 — 发现硬性排除条款但未在前序步骤中过滤
    │   └─ 如发现 ❌ 冲突, 将该试验从高度匹配移至条件匹配或排除
    │
    └─ 6d. 输出校验摘要
        ├─ 校验通过: 报告发布
        ├─ 发现问题: 修正后重新生成报告, 再次校验
        └─ 校验摘要附在报告 footer 中 (如: "9 项试验 ID 已验证, 0 项无效")
```

### 脚本文件

检索脚本位于: `repo/retrieval/dual_source_search.py`

```bash
# 独立运行 (LLM 先生成 search_plan.json)
python repo/retrieval/dual_source_search.py \
  --plan search_plan.json \
  --out results.json \
  --max-per-query 10

# 或作为模块被 LLM agent 调用
from retrieval.dual_source_search import execute_search_plan, generate_search_plan_prompt
results = execute_search_plan(plan_dict, chictr_results=chictr_data)
```

## 前置依赖检查

在执行本 skill 之前，**必须先检查 chictr-mcp-server 是否已安装并配置**。

> **为什么需要 chictr-mcp-server?**
> chictr.org.cn 不提供公开 JSON API、且页面需要 JS 渲染并有反爬保护，
> 所以无法用纯 HTTP 抓取。chictr-mcp-server (TypeScript + Puppeteer)
> 已经处理了浏览器自动化、验证码检测、会话管理、熔断器等问题，是当前
> 最可靠的 ChiCTR 接入方式。本 skill 不内嵌它，但会自动帮用户把它注册
> 到 Claude Code 的 MCP 配置里。

### 自动检测流程

执行本 skill 时，按以下步骤检测 chictr-mcp-server 是否可用：

1. **检查 MCP 工具是否可用**: 尝试调用 `mcp__chictr__search_trials`。如果工具存在且可调用，跳到工作流 Step 1。
2. **如果工具不可用**: 让用户运行 skill 自带的一键安装脚本（见下），脚本完成后必须 **重启 Claude Code 会话** 才能让新 MCP server 生效。
3. **如果用户拒绝安装或安装失败**: 降级到仅 ClinicalTrials.gov，并在最终报告中明确标注 "ChiCTR 数据源不可用"。

### 一键安装（推荐用户执行）

skill 仓库自带 `scripts/setup-chictr-mcp.sh`，幂等，可重复执行。它会：

- 检查 Node.js ≥ 18
- 在 `~/.claude.json` 的 `mcpServers` 里 add/merge `chictr` 条目
- 通过 `npx -y chictr-mcp-server` 验证 npm 包可达
- 引导用户重启 Claude Code

```bash
# 从 skill 安装目录运行
bash ~/.claude/skills/clinical-trial-matching-skill/scripts/setup-chictr-mcp.sh

# 或从 git clone 下来的仓库运行
bash scripts/setup-chictr-mcp.sh
```

执行成功后，**关闭并重开 Claude Code 会话**，再次调用本 skill 时会自动检测到 MCP 工具。

### 手动配置（如果一键脚本不可用）

> 仅当 `setup-chictr-mcp.sh` 执行失败时使用。

在全局 `~/.claude.json` 或项目根目录的 `.mcp.json` 中添加：

```json
{
  "mcpServers": {
    "chictr": {
      "command": "npx",
      "args": ["-y", "chictr-mcp-server"]
    }
  }
}
```

### 验证安装

重启 Claude Code 会话后，验证以下工具是否可用：
- `mcp__chictr__search_trials` — 搜索 ChiCTR 临床试验
- `mcp__chictr__get_trial_detail` — 查询试验详情

如果工具不可用，请检查：
- Node.js 版本 ≥ 18 (`node --version`)
- npx 命令在 PATH 中 (`which npx`)
- 网络可访问 npmjs.com 和 chictr.org.cn

## 使用示例

### 输入

患者病情信息（以下任一形式均可）:

- 结构化病历文件: `.docx` / `.pdf` / `.json`
- 自由文本: 直接粘贴的病情描述
- 病历文件夹路径: `~/patients/{patient}/`

示例输入:
```
诊断: 乙状结肠中分化腺癌 IV期, 双肺转移
分子特征: KRAS G12C, MSS, ATM胚系突变
治疗线数: 5线 (已用过化疗/靶向/免疫)
```

### 输出

一份独立的 HTML 报告文件，保存到 `~/Downloads/临床试验匹配报告_TrialGPT_{日期}.html`

报告包含 7 个板块:
1. **患者画像摘要** — 4 列 grid 卡片，分子特征用彩色标签
2. **治疗决策史** — 可视化时间线，疗效用 badge 标注
3. **系统匹配结论** — 一段话总结检索过程和结果
4. **候选试验清单** — 高度匹配 + 条件匹配两张表，ID 可点击跳转
5. **已排除方向** — 不匹配的试验及排除原因
6. **临床中心信息** — 3 列卡片，医院/研究者/SMO 说明
7. **信息缺口 + 行动清单** — 紧急/重要/建议分级 + checkbox 行动项

## Guardrails

- ClinicalTrials.gov 数据默认筛选 RECRUITING 状态
- ChiCTR 数据筛选"正在招募"状态
- **本工具仅提供信息匹配，不构成临床建议或治疗推荐**
- 所有入组资格需由临床研究团队最终审核确认
- 保留 prompt/config 元数据用于审计
- 明确告知用户试验来源，避免混淆
- **治疗线数为硬筛选条件**: 仅限一线的试验对多线患者直接排除，不展示
- **ECOG 默认符合**: 除非试验明确要求 ECOG=0 且有理由认为患者不满足，否则不标注为风险
- **对外文档不含评分、不使用"推荐"、不含优先级排序**

## References

- TrialGPT: https://github.com/ncbi-nlp/TrialGPT
- ChiCTR MCP Server: https://github.com/PancrePal-xiaoyibao/chictr-mcp-server
- ChiCTR 官网: https://www.chictr.org.cn/


