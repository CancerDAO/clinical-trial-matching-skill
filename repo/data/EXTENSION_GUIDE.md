# Clinical Ontology Extension Guide (v1.7.1+)

## Generalization principle

> **Code is mechanism. Data is knowledge.**
>
> Adding support for a new cancer type, drug class, or chemo regimen MUST NOT require code changes.
> All cancer-specific knowledge lives in the four data files in this directory:
>
> - `clinical_ontology.json` — cancer aliases, anti-aliases, OS thresholds, chemo regimens, therapy classes, biomarker aliases
> - `risk_taxonomy.json` — mechanism × cancer risk profiles
> - `efficacy_database.json` — NCT-level + drug-class efficacy snapshots
> - `soc_benchmarks.json` — standard-of-care per cancer × line × molecular subtype

## Quick check: is your change generalizable?

Before editing **code**, ask:

| If you're tempted to... | The right place is |
|---|---|
| Add a drug name to a list inside `.py` | `risk_taxonomy.json` `drug_keywords` OR `clinical_ontology.json` `prior_therapy_classes` |
| Add a cancer-specific alias / anti-alias | `clinical_ontology.json` `cancers.X.aliases` / `anti_aliases` |
| Hard-code a chemo regimen name in matching logic | `clinical_ontology.json` `chemo_regimens.<name>.components` |
| Hard-code an "if cancer == 'PDAC'" branch | Pull the threshold or rule from ontology; if missing, fall back to `_default` |
| Add an OS threshold for a (cancer, line) pair | `clinical_ontology.json` `cancers.X.median_os_months_at_line` |

If the rule is genuinely about **mechanism** (e.g., "TCR-T trials need HLA typing"), code is OK. If the rule is about **a specific cancer or drug**, push it to data.

## How to add support for a new cancer (e.g., breast cancer)

### Step 1 — Cancer profile (required)

Edit `clinical_ontology.json`:

```json
"cancers": {
  "BREAST": {
    "full_names": ["Breast Cancer", "breast carcinoma"],
    "aliases": ["breast", "mammary", "btc"],
    "anti_aliases": ["male breast", "phyllodes"],
    "common_metastasis_sites": ["bone", "liver", "lung", "brain", "lymph node"],
    "median_os_months_at_line": {
      "0_naive_metastatic_her2_pos": 57.0,
      "0_naive_metastatic_hr_pos": 50.0,
      "0_naive_metastatic_tnbc": 18.0,
      "1_post_first_line": 24.0,
      "2_post_second_line": 14.0,
      "3_plus": 9.0
    },
    "goc_trigger_threshold_months": 9.0,
    "rapid_progression_cycles_threshold": 4
  }
}
```

### Step 2 — Therapy classes specific to this cancer (recommended)

Edit `clinical_ontology.json` `prior_therapy_classes`:

```json
"CDK4/6 inhibitor": {
  "components": ["palbociclib", "ribociclib", "abemaciclib", "dalpiciclib"]
},
"PI3K inhibitor (breast)": {
  "components": ["alpelisib", "inavolisib"]
}
```

### Step 3 — SoC benchmarks (recommended)

Edit `soc_benchmarks.json` `benchmarks.BREAST`:

```json
"BREAST": {
  "metastatic_1L_hr_pos": [
    {"regimen": "Letrozole + Palbociclib", "pivotal": "PALOMA-2", "median_os_months": 53.9, "orr": 0.42}
  ],
  "metastatic_2L_post_cdk46": [
    {"regimen": "Inavolisib + Fulvestrant + Palbociclib", "pivotal": "INAVO120", "median_os_months": 31.0}
  ],
  "metastatic_2L_post_egfr_tki": [...]
}
```

### Step 4 — Risk profiles (optional but recommended)

Edit `risk_taxonomy.json` to add breast-specific mechanism risks (e.g., CDK4/6 sequential, T-DXd ILD risk).

### Step 5 — Validate

Run on a synthetic patient:

```bash
python3 repo/data/ontology_loader.py  # self-test
```

Add a golden test case (`repo/eval/golden_cases/case_NN_breast_*.json`) and run:

```bash
python3 repo/eval/runner.py --case eval/golden_cases/case_NN_breast_*.json --gated ... --report ...
```

## How to add a new chemo regimen (e.g., adding "ENHERTU" pattern)

Edit `clinical_ontology.json` `chemo_regimens`:

```json
"T-DXd alone": {
  "components": ["trastuzumab deruxtecan", "ds-8201", "t-dxd", "enhertu"],
  "common_in": ["breast HER2+", "gastric HER2+"]
}
```

Now any trial whose interventions list contains "T-DXd" + a patient who received "enhertu" prior will fire `chemo_backbone_overlap` automatically.

## How to add a new prior-therapy class (e.g., "ADC class")

Edit `clinical_ontology.json` `prior_therapy_classes`:

```json
"HER2-low ADC": {
  "components": ["trastuzumab deruxtecan", "datopotamab deruxtecan", "u3-1402"]
}
```

The gating layer's `evaluate_targeted_therapy_class_overlap` will automatically detect class overlap for ANY trial × patient pairing using this class.

## What you should NEVER do

- ❌ Hard-code a drug name in `gating.py`, `decision_paths.py`, `feasibility.py`, `consistency_check.py`, or `goals_of_care.py`
- ❌ Add `if cancer == "X"` branches in code (use ontology lookup)
- ❌ Tune thresholds (chemo overlap penalty, demotion ratios) without checking they make sense for at least 2 different cancers
- ❌ Rely on a single golden case to validate behavior — at minimum run all 3 (PDAC G12D, PDAC G12C, NSCLC EGFR) before declaring a change correct
- ❌ Add an alias to `clinical_ontology.json` `cancers._default` that's specific to one cancer — `_default` is the catch-all for unknown cancers

## What WILL break gracefully

Behavior with a totally unknown cancer (e.g., Mesothelioma without an ontology entry):

- ✅ Pipeline runs without error
- ✅ `_default` cancer profile applies (12-month OS threshold, generic 6-month GoC)
- ✅ Cancer-type strict gate falls back to wildcard match ("solid tumor" / mutation-defined)
- ⚠️ SoC benchmarks empty → vs-SoC comparison shows "Not available"
- ⚠️ Risk profile falls back to mechanism-only matching (overseas / Phase 1 / cell-therapy still fire)
- ⚠️ Decision paths still produced; consistency flags still fire

## Data files cross-reference

| File | What goes here | What does NOT go here |
|---|---|---|
| `clinical_ontology.json` | Cancer aliases, OS thresholds, chemo regimens, therapy classes, biomarkers | Trial-specific data, risk narratives, efficacy numbers |
| `risk_taxonomy.json` | Mechanism × cancer risk narratives, drug-class metadata | OS / PFS numbers, SoC benchmarks |
| `efficacy_database.json` | Per-NCT or per-drug ORR / PFS / OS with citation | Risk narratives, eligibility patterns |
| `soc_benchmarks.json` | Standard-of-care per cancer × line × molecular subtype | Trial-specific data, drug class info |

## Schema validation

When extending, run the loader self-test to catch obvious schema breakage:

```bash
python3 repo/data/ontology_loader.py
```

Expected output ends with `✅ ontology_loader self-test passed`.
