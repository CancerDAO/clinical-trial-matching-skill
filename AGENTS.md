# AGENTS.md

Guidance for AI coding agents (Claude Code, Cursor, Copilot, Codex, etc.) working on this codebase.

## Repository overview

This is a Claude Code / agent skill collection for matching oncology patients to clinical trials. v2 is structured as one parent orchestrator skill + 4 LLM subskills + a thin Python mechanism layer.

## Project layout

```
skills/
  clinical-trial-matching/         # parent orchestrator
    scripts/                        # KEEP minimal — only deterministic mechanism
    data/                           # KEEP slim — aliases + chemo regimens only
    examples/                       # patient + search_plan worked examples
  trial-gater/                      # LLM subskill
    rules/                          # one rule per file, prefix-named
  trial-risk-annotator/             # LLM subskill
  trial-efficacy-contextualizer/    # LLM subskill
  decision-synthesizer/             # LLM subskill
```

## When adding a new feature

### Decision tree: Code change or rule change?

```
Is this change about HOW we mechanically retrieve/verify/render data?
  YES → Python change (in scripts/)
  NO ↓

Is this change about WHAT clinical knowledge applies?
  YES → Rule change (in skills/{subskill}/rules/)

Common cases:
  - Add new cancer type's SoC                  → rules change (trial-efficacy-contextualizer/rules/soc-{cancer}-by-line.md)
  - Add new drug class                          → rules change (trial-risk-annotator/rules/risk-{class}.md + clinical_ontology.therapy_classes)
  - Add new pivotal trial efficacy data         → rules change (cite in soc-{cancer}-by-line.md)
  - Fix a regex parsing bug in eligibility text → Python change (dual_source_search.py)
  - Add a new feasibility dimension             → Python change (feasibility.py) — note: new "dimensions" rarely justified
  - Adjust GoC trigger threshold                → rules change (synthesis-goals-of-care-trigger.md)
  - Fix HTML rendering issue                    → Python change (html_renderer.py + template.html)
```

### Naming conventions

- **Skill directory**: `kebab-case` (e.g. `trial-gater`)
- **SKILL.md**: always uppercase exact filename
- **Rule files**: `{prefix}-{descriptor}.md` (e.g. `R1-prior-same-class-drug.md`, `risk-kras-g12c-by-cancer.md`, `soc-crc-by-line.md`)
- **Output schema files**: `output-{topic}-schema.md`
- **Python files**: `snake_case.py`

### SKILL.md frontmatter format

```yaml
---
name: skill-name-kebab-case
description: |
  Use when [specific triggering conditions]. [What it does in 1-2 sentences.]
license: MIT
metadata:
  author: CancerDAO
  version: "2.0.0"
  parent_skill: clinical-trial-matching   # for subskills only
---
```

The `description` MUST start with "Use when..." and describe triggering conditions. Do NOT summarize the skill's process there — that goes in SKILL.md body.

## When NOT to add to scripts/

The following are tempting but wrong:

- **Don't** add a new entry to a JSON lookup table for clinical knowledge. Move the knowledge to a subskill rule file.
- **Don't** add regex pattern matching for trial eligibility text. The trial-gater subskill is the LLM that reads eligibility text — let it do the judgment.
- **Don't** add a `risk_taxonomy_v2.json`. Risk narratives belong in `trial-risk-annotator/rules/risk-*.md`.
- **Don't** add `efficacy_database_extended.json`. Efficacy belongs in `trial-efficacy-contextualizer/rules/soc-*.md`.

## Testing

- **End-to-end smoke test**: use the worked example at `skills/clinical-trial-matching/examples/PT-17CE02BC33-*.json`. Run the parent skill against it and inspect the HTML.
- **Subskill unit testing**: each subskill has an `examples/` directory (TODO: populate). Invoke the subskill standalone with a known input, compare output JSON against expected.
- **Regression tests for known v1.7.x bugs** (must pass on v2):
  - PT-17CE02BC33 should NOT see "PDAC" in any risk narrative
  - PT-17CE02BC33 should NOT see "KRAS G12D inhibitor" class baseline
  - PT-17CE02BC33 SHOULD see CRC L3+ SoC options (regorafenib, TAS-102+bev, fruquintinib, sotorasib+panitumumab)
  - PT-17CE02BC33 SHOULD trigger Goals-of-Care section
  - PT-17CE02BC33 SHOULD have R1 demotion on trials excluding prior anti-PD-1 or anti-VEGF

## Style

- Markdown rule files: keep under 200 lines each. Split into multiple files when topic grows.
- Python scripts: stdlib only where possible (current state). If a dependency is added, document why mechanism couldn't stay pure Python.
- Output schemas: prefer JSON examples with `// inline comments` over prose schema descriptions.

## Commit messages

Follow the existing project convention (Conventional Commits):

- `feat(skill-name): add X`
- `fix(rule): correct Y in soc-crc-by-line.md`
- `docs(readme): update install instructions`
- `refactor(scripts): extract feasibility scoring into module`
- `chore: bump version to v2.x.x`
