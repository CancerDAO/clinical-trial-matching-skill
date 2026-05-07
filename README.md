# Clinical Trial Matching Skill (v2)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

A Claude Code / Anthropic Agent **Skill** for matching oncology patients (especially Chinese patients) to clinical trials with **dual-source retrieval** (ClinicalTrials.gov + ChiCTR) and **LLM-driven clinical reasoning**.

> ⚠️ **This tool provides information matching only. It does not constitute medical advice or treatment recommendations.** All enrollment decisions must be reviewed by a qualified clinical research team.

---

## What's new in v2

v2 is a ground-up architecture refactor. v1.7.x packed clinical knowledge into Python lookup tables (`risk_taxonomy.json`, `efficacy_database.json`, `soc_benchmarks.json`, regex matchers in `gating.py`). That approach **could not generalize** — adding a cancer type or fixing a mechanism×cancer mismatch required code changes, and the data tables were never complete enough.

**v2 split**: keep Python for *mechanism* (deterministic things), move *knowledge* into LLM subskills.

| Layer | v1.7.x | v2 |
|---|---|---|
| Retrieval (parallel HTTP, dedup) | Python | **Python (kept)** |
| NCT verification (live API) | Python | **Python (kept)** |
| 5-dim feasibility scoring | Python | **Python (kept)** |
| HTML render (template fill) | Python | **Python (kept)** |
| Per-trial gating + R1-R5 | `scoring/gating.py` (regex) | `trial-gater` LLM subskill |
| Risk narratives (mechanism × cancer) | `risk_taxonomy.json` lookup | `trial-risk-annotator` LLM subskill |
| Efficacy + SoC comparison | `efficacy_database.json` + `soc_benchmarks.json` | `trial-efficacy-contextualizer` LLM subskill |
| Top-N decision paths + GoC trigger | `synthesis/decision_paths.py` + `goals_of_care.py` | `decision-synthesizer` LLM subskill |

### Bugs v2 was built to fix

A v1.7.1 test run on a real KRAS G12C mCRC patient (PT-17CE02BC33) surfaced these failures:

1. PDAC-specific risk text leaked onto a CRC report ("EGFR antibody combination (PDAC)" risk attached to a CRC patient on Calderasib trial)
2. KRAS G12D drug-class baseline (ORR 30%, mPFS 4.5mo) applied to a KRAS G12C patient as their #3 decision path
3. CRC SoC database had only 1 entry (BRAF V600E 1L) → all CRC patients got "vs SoC: not available"
4. R1 hard rule (prior same-class drug → demote) silently failed for patients with prior anti-PD-1 / anti-VEGF
5. Goals-of-Care trigger didn't fire for a 69yo IV CRC patient on L3 with severe CV comorbidity (used `treatment_lines_completed=2` instead of effective L3)
6. Pipeline `cd` bug in `run_v160_pipeline.sh` broke relative `--patient` path
7. `decision_report.json` had `patient_summary={}` and `feasibility_score=None` despite stdout printing correct values

v2 architecture prevents these by design:
- All risk narratives must declare `applies_because: (mechanism × cancer × patient)` — schema enforces it
- All efficacy claims must declare `evidence_source.tier` and `applies_because` — class baselines must match patient's mutation
- SoC comparison is generated from per-cancer rule files (`rules/soc-{cancer}-by-line.md`) — no hardcoded JSON gap
- R1-R5 are LLM judgments with explicit drug-class reference, not regex
- GoC trigger uses `effective_line = treatment_lines_completed + (1 if current_therapy_ongoing else 0)`

---

## Repository structure

Following the [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) convention:

```
clinical-trial-matching-skill/
├── README.md                                  # this file
├── AGENTS.md                                  # guidance for AI agents working on this codebase
├── CHANGELOG.md
├── LICENSE                                    # MIT
├── NOTICE.md                                  # attribution to NCBI TrialGPT
└── skills/
    ├── clinical-trial-matching/               # PARENT orchestrator skill
    │   ├── SKILL.md
    │   ├── data/
    │   │   └── clinical_ontology.json         # cancer aliases + chemo regimens + therapy classes (NO efficacy/risk/SoC)
    │   ├── scripts/                           # mechanism (deterministic Python)
    │   │   ├── retrieval/
    │   │   │   ├── dual_source_search.py
    │   │   │   └── chictr_resilient.py
    │   │   ├── verification/nct_verifier.py
    │   │   ├── scoring/feasibility.py         # 5-dim deterministic
    │   │   ├── render/
    │   │   │   ├── html_renderer.py
    │   │   │   └── template.html
    │   │   └── setup-chictr-mcp.sh
    │   └── examples/
    │       ├── PT-17CE02BC33-patient.json
    │       └── PT-17CE02BC33-search-plan.json
    ├── trial-gater/                           # SUBSKILL: criterion eligibility + R1-R5
    │   ├── SKILL.md
    │   └── rules/
    │       ├── R1-prior-same-class-drug.md
    │       ├── R2-treatment-line-mismatch.md
    │       ├── R3-indication-scope.md
    │       ├── R4-organ-function-borderline.md
    │       ├── R5-missing-critical-fields.md
    │       └── output-gating-verdict-schema.md
    ├── trial-risk-annotator/                  # SUBSKILL: per (mechanism × cancer) risk narratives
    │   ├── SKILL.md
    │   └── rules/
    │       ├── risk-kras-g12c-by-cancer.md
    │       ├── risk-kras-g12d-class.md
    │       ├── risk-pan-ras-class.md
    │       ├── risk-cell-therapy-solid-tumor.md
    │       ├── risk-bispecific-mss-crc.md
    │       ├── risk-phase-1-dose-escalation.md
    │       └── output-risk-annotation-schema.md
    ├── trial-efficacy-contextualizer/         # SUBSKILL: efficacy + per-line SoC
    │   ├── SKILL.md
    │   └── rules/
    │       ├── soc-crc-by-line.md
    │       ├── soc-nsclc-by-line.md
    │       ├── soc-pdac-by-line.md
    │       └── output-efficacy-context-schema.md
    └── decision-synthesizer/                  # SUBSKILL: Top-N + GoC + diversity
        ├── SKILL.md
        └── rules/
            ├── synthesis-diversity-bucketing.md
            ├── synthesis-goals-of-care-trigger.md
            └── output-decision-report-schema.md
```

---

## Installation

### Option A — install all 5 skills via `npx skills` (recommended)

```bash
npx skills add CancerDAO/clinical-trial-matching-skill --skill clinical-trial-matching --skill trial-gater --skill trial-risk-annotator --skill trial-efficacy-contextualizer --skill decision-synthesizer
```

Or install all skills in this repo:

```bash
npx skills add CancerDAO/clinical-trial-matching-skill --skill '*'
```

### Option B — manual install

```bash
git clone https://github.com/CancerDAO/clinical-trial-matching-skill.git
cp -r clinical-trial-matching-skill/skills/* ~/.claude/skills/
```

### One-time: ChiCTR MCP setup

```bash
bash skills/clinical-trial-matching/scripts/setup-chictr-mcp.sh
```

Then restart Claude Code.

---

## Usage

Invoke from any Claude Code conversation:

```
帮我做临床试验匹配:
诊断: 乙状结肠中分化腺癌 IV期, 双肺/肝转移
分子特征: KRAS G12C, MSS, TMB 7.7
治疗线数: 已完成2线 (mFOLFOX6 PR; KELOX+卡瑞利珠+阿帕替尼 PD), 当前三线 KELOX+卡瑞利珠+贝伐进行中
心血管基础疾病严重 (HTN3 + CAD + 支架术后)
```

The parent `clinical-trial-matching` skill triggers automatically. It runs the 9-step pipeline (retrieval → metadata → gating → verification → feasibility → risk → efficacy → synthesis → render) and emits `~/Downloads/临床试验匹配报告_{patient_id}_{date}.html`.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ PARENT: clinical-trial-matching                                    │
│   1. Read patient summary → patient.json (LLM)                     │
│   2. Generate search_plan.json (LLM, 8-dim keyword strategy)       │
│   3. Python: dual_source_search.py → nct_results.json              │
│   4. MCP: ChiCTR query (mcp__chictr__*) → merge                    │
│   5. Python: nct_verifier.py → verified.json (live API check)      │
│   6. Python: feasibility.py → scored.json (5-dim deterministic)    │
│                                                                     │
│      ↓ for each candidate trial, dispatch subagent batches:        │
│                                                                     │
│   7a. → trial-gater          (gating verdict + R1–R5)              │
│   7b. → trial-risk-annotator  (mechanism × cancer × patient)        │
│   7c. → trial-efficacy-contextualizer (efficacy + per-line SoC)     │
│                                                                     │
│      ↓ aggregate, then:                                            │
│                                                                     │
│   8. → decision-synthesizer   (Top-N paths + GoC + diversity)      │
│   9. Python: html_renderer.py → report.html                        │
└───────────────────────────────────────────────────────────────────┘
```

---

## Generalization principle

> **Code is mechanism. Knowledge is in subskills.**
>
> Adding a new cancer type, drug class, or risk narrative MUST be done by editing a rule file in the relevant subskill (`skills/{subskill}/rules/*.md`), never by editing Python code or extending a JSON lookup table.
>
> If you find yourself adding a new branch to `feasibility.py` or a new entry to `clinical_ontology.json` for a clinical-knowledge reason — STOP. That belongs in a subskill rule file.

---

## License & attribution

- **CancerDAO additions** (skill definitions, dual-source orchestrator, HTML report, workflow, subskill architecture): [MIT](./LICENSE)
- **NCBI TrialGPT** inspired the 8-dimension keyword strategy and criterion-level CoT pattern. The original Python package is not vendored. See [`NOTICE.md`](./NOTICE.md).
- **chictr-mcp-server** is an external dependency: [PancrePal-xiaoyibao/chictr-mcp-server](https://github.com/PancrePal-xiaoyibao/chictr-mcp-server).

---

## Contributing

Issues and PRs welcome at <https://github.com/CancerDAO/clinical-trial-matching-skill>.

When adding a new cancer type's SoC rules or risk narratives, please:
1. Create the rule file in the appropriate subskill's `rules/` directory
2. Cross-reference any pivotal trials with PMID
3. Note evidence tier explicitly
4. Add a worked example in the parent skill's `examples/`

Built by [CancerDAO](https://github.com/CancerDAO) — open-source AI for cancer patients.
