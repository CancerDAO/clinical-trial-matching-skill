# Clinical Trial Matching Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

A Claude Code / Anthropic Agent **Skill** for matching oncology (and other)
patients to clinical trials with **dual-source retrieval** across
[ClinicalTrials.gov](https://clinicaltrials.gov) and
[ChiCTR](https://www.chictr.org.cn) (中国临床试验注册中心).

Built on top of [NCBI TrialGPT](https://github.com/ncbi-nlp/TrialGPT) and
extended by [CancerDAO](https://github.com/CancerDAO) with a Chinese
clinical workflow, a criterion-level chain-of-thought evaluator, hard
grading rules, a three-stage verification pipeline, and a self-contained
HTML report.

> ⚠️ **This tool provides information matching only. It does not constitute
> medical advice or treatment recommendations.** All enrollment decisions
> must be reviewed by a qualified clinical research team.

---

## What's in this repo

```
clinical-trial-matching-skill/
├── SKILL.md                    # The skill definition (drop into ~/.claude/skills/)
├── LICENSE                     # MIT
├── NOTICE.md                   # Attribution
├── scripts/
│   └── setup-chictr-mcp.sh    # One-command ChiCTR MCP installer
└── repo/
    ├── README.md               # Notes on the (now-removed) NCBI lineage
    ├── retrieval/
    │   └── dual_source_search.py      # Parallel NCT + ChiCTR search (stdlib only)
    └── report/
        └── template.html              # 8-section HTML report
```

All LLM reasoning (keyword generation, criterion-level evaluation,
ranking, report writing) is performed by Claude in the conversation —
the only Python in this repo is the parallel HTTP retriever, which uses
nothing but the standard library.

---

## Key enhancements over upstream TrialGPT

| | Upstream TrialGPT | This skill |
|---|---|---|
| **Data sources** | ClinicalTrials.gov | ClinicalTrials.gov **+ ChiCTR** |
| **Keyword strategy** | Single LLM pass | **8-dimension strategy**: disease-specific, generalized ("solid tumor"), cell-therapy-unconstrained, combo targets, pathway, Chinese keywords, **exhaustive drug-name enumeration**, **resistance-pathway keywords** |
| **Eligibility check** | Criterion-level matching | Same + **hard grading rules R1–R5** to prevent "high match" inflation (prior same-class drug, line-of-therapy mismatch, indication scope, organ-function borderline, ≥2 missing critical fields) |
| **Verification** | — | **3-stage post-hoc verification**: (a) NCT/ChiCTR ID validation via official APIs, (b) completeness review against the search plan, (c) patient-fit re-check |
| **Output** | JSON | **Self-contained HTML report** (8 sections: patient profile, treatment timeline, matching summary, ranked candidates, criterion-by-criterion assessment, excluded directions, referral centers, information gaps + action items) |
| **Compliance** | — | No scores, no "recommend", no priority ranking in patient-facing output |

---

## Installation

### 1. Install the skill

Drop the skill into your Claude Code skills directory:

```bash
git clone https://github.com/CancerDAO/clinical-trial-matching-skill.git
mkdir -p ~/.claude/skills/clinical-trial-matching-skill
cp -r clinical-trial-matching-skill/SKILL.md \
      clinical-trial-matching-skill/repo \
      clinical-trial-matching-skill/scripts \
      ~/.claude/skills/clinical-trial-matching-skill/
```

To invoke the skill, just describe the task in natural language —
"帮我做临床试验匹配", "shortlist trials for this patient", etc. Claude
will trigger it on `description` match. No slash command is needed.

### 2. (Skipped — no Python deps to install)

The dual-source retriever (`repo/retrieval/dual_source_search.py`)
uses only Python stdlib (`urllib`, `concurrent.futures`). All LLM reasoning
— keyword generation, criterion-level eligibility evaluation, ranking,
and report writing — is performed by Claude in the conversation, not by a
separate Python LLM client. So there is **no `pip install` step and no
LLM API key to configure**. Just make sure you have Python 3.9+ on PATH.

> Earlier releases vendored the upstream NCBI TrialGPT package, which
> shipped 13 Python deps (torch / faiss / transformers / openai / …) and
> required an Azure OpenAI key. None of that code was actually invoked by
> the skill workflow, so it has been removed.

### 3. Register the ChiCTR MCP server (one command)

The ChiCTR data source is provided by the external
[chictr-mcp-server](https://github.com/PancrePal-xiaoyibao/chictr-mcp-server)
(TypeScript + Puppeteer). It can't be vendored into this repo — chictr.org.cn
has no public JSON API and requires real browser automation for queries.

Run the bundled installer instead of editing `~/.claude.json` by hand:

```bash
bash scripts/setup-chictr-mcp.sh
```

The script is idempotent. It:

1. Verifies Node.js ≥ 18 is on PATH (npx ships with Node).
2. Adds (or no-ops if present) the `chictr` entry under `mcpServers` in
   `~/.claude.json` — keeps every other MCP server you have untouched.
3. Smoke-tests `npx -y chictr-mcp-server` to confirm the npm package is
   reachable.

Then **restart Claude Code** so it picks up the new MCP server, and verify
these tools appear in a new session:

- `mcp__chictr__search_trials`
- `mcp__chictr__get_trial_detail`

If you'd rather configure manually, the equivalent JSON is:

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

If the MCP server is unavailable, the skill will degrade gracefully and run
with ClinicalTrials.gov only.

---

## Usage

Once installed, invoke the skill from any Claude Code conversation with a
patient summary:

```
诊断: 乙状结肠中分化腺癌 IV期, 双肺转移
分子特征: KRAS G12C, MSS, ATM 胚系突变
治疗线数: 5 线 (已用过化疗/靶向/免疫)
```

The skill will:

1. Extract the patient profile
2. Generate an 8-dimension search plan
3. Run parallel retrieval against ClinicalTrials.gov + ChiCTR
4. Score candidates with criterion-level CoT + hard rules R1–R5
5. Verify every NCT/ChiCTR ID against official APIs
6. Emit an HTML report to `~/Downloads/`

See `SKILL.md` for the full workflow and prompt contract.

---

## License & attribution

- **CancerDAO additions** (the skill definition, dual-source orchestrator,
  HTML report, workflow, hard grading rules R1–R5, and the criterion-level
  CoT prompt contract): [MIT](./LICENSE)
- **NCBI TrialGPT** (Jin Q. et al., *Matching Patients to Clinical Trials
  with Large Language Models*): the original NCBI Python package is no
  longer vendored — its retrieval/matching/ranking modules were unused by
  this skill's workflow. The 8-dimension keyword strategy and
  criterion-level evaluation pattern are conceptually inspired by their
  paper; please cite it if you build on this work.
- **chictr-mcp-server** is an external dependency maintained by
  [PancrePal-xiaoyibao](https://github.com/PancrePal-xiaoyibao/chictr-mcp-server).

See [`NOTICE.md`](./NOTICE.md) for details.

---

## Contributing

Issues and PRs welcome at
<https://github.com/CancerDAO/clinical-trial-matching-skill>.

Built by [CancerDAO](https://github.com/CancerDAO) —
open-source AI for cancer patients.
