"""
efficacy_lookup.py — v1.6.0 P1.1

Look up efficacy snapshot for a trial. Tries:
  1. Exact NCT ID match in efficacy_database.json
  2. Drug name match (any intervention)
  3. Drug class baseline (KRAS G12D inhibitor, Pan-RAS, etc.)

Returns None if no match (which the synthesizer will flag as "data unavailable").
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATH = Path(__file__).parent.parent / "data" / "efficacy_database.json"
_cache = None


def load_db(path: str | Path = DEFAULT_PATH) -> dict:
    global _cache
    if _cache is None:
        with open(path, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def lookup_efficacy(trial: dict, db: dict | None = None) -> Optional[dict]:
    if db is None:
        db = load_db()

    nct_id = trial.get("id", "")
    interventions = trial.get("interventions", [])
    interventions_lower = [iv.lower() for iv in interventions]
    title_lower = trial.get("title", "").lower()

    # 1. Exact NCT match
    for s in db.get("snapshots", []):
        if s.get("nct_id") == nct_id:
            return {**s, "match_type": "exact_nct"}

    # 2. Drug match
    for s in db.get("snapshots", []):
        drug = s.get("drug", "").lower()
        if not drug:
            continue
        # Match against interventions or title
        # Drug aliases: ASP3082 ≈ Setidegrasib, RMC-6236 ≈ daraxonrasib
        aliases = [drug]
        if "asp3082" in drug or "setidegrasib" in drug:
            aliases += ["asp3082", "setidegrasib"]
        if "rmc-6236" in drug or "daraxonrasib" in drug:
            aliases += ["rmc-6236", "daraxonrasib"]

        for alias in aliases:
            if any(alias in iv for iv in interventions_lower) or alias in title_lower:
                return {**s, "match_type": "drug_match"}

    # 3. Drug class fallback
    risk_keys = [r.get("key") for r in trial.get("risk_profiles", [])]
    class_baselines = db.get("drug_class_baselines", {})

    if "kras_g12d_first_in_class" in risk_keys:
        if "KRAS G12D inhibitor (small molecule)" in class_baselines:
            return {
                "match_type": "drug_class_baseline",
                "drug_class": "KRAS G12D inhibitor (small molecule)",
                "metrics": class_baselines["KRAS G12D inhibitor (small molecule)"],
                "source": {"citation": "Class baseline from published Phase I/II KRAS G12D programs"},
                "maturity": "class_estimate",
                "caveats": "Trial-specific data unavailable; class baseline used. Confirm with sponsor at screening visit."
            }
    if "pan_ras_inhibitor" in risk_keys:
        if "Pan-RAS inhibitor" in class_baselines:
            return {
                "match_type": "drug_class_baseline",
                "drug_class": "Pan-RAS inhibitor",
                "metrics": class_baselines["Pan-RAS inhibitor"],
                "source": {"citation": "Class baseline from published Pan-RAS programs"},
                "maturity": "class_estimate",
            }
    if "tcr_t_pdac" in risk_keys:
        if "TCR-T (against KRAS neoantigen)" in class_baselines:
            return {
                "match_type": "drug_class_baseline",
                "drug_class": "TCR-T (against KRAS neoantigen)",
                "metrics": class_baselines["TCR-T (against KRAS neoantigen)"],
                "source": {"citation": "Class baseline from PDAC-restricted TCR-T programs"},
                "maturity": "class_estimate",
            }
    if "til_solid_tumor" in risk_keys:
        if "TIL therapy (PDAC)" in class_baselines:
            return {
                "match_type": "drug_class_baseline",
                "drug_class": "TIL therapy (PDAC)",
                "metrics": class_baselines["TIL therapy (PDAC)"],
                "source": {"citation": "Class baseline from PDAC TIL programs"},
                "maturity": "class_estimate",
            }
    if "claudin18_2_targeted" in risk_keys or "msln_targeted" in risk_keys:
        # CAR-T or ADC
        if "CT041" in trial.get("interventions", []) or "satricabtagene" in title_lower:
            if "CAR-T (CLDN18.2 / MSLN)" in class_baselines:
                return {
                    "match_type": "drug_class_baseline",
                    "drug_class": "CAR-T (CLDN18.2 / MSLN)",
                    "metrics": class_baselines["CAR-T (CLDN18.2 / MSLN)"],
                    "source": {"citation": "Class baseline"},
                    "maturity": "class_estimate",
                }
        elif "ADC (CLDN18.2 / TROP2)" in class_baselines:
            return {
                "match_type": "drug_class_baseline",
                "drug_class": "ADC (CLDN18.2 / TROP2)",
                "metrics": class_baselines["ADC (CLDN18.2 / TROP2)"],
                "source": {"citation": "Class baseline"},
                "maturity": "class_estimate",
            }

    return None


def get_soc_for(patient: dict, soc_path: str | Path = None) -> list[dict]:
    """
    Return list of relevant SoC regimens for the patient.
    v1.7.1 generalization: cancer-type normalization comes from ontology.
    Routing logic uses ontology's chemo regimen detection.
    """
    if soc_path is None:
        soc_path = Path(__file__).parent.parent / "data" / "soc_benchmarks.json"
    with open(soc_path, "r", encoding="utf-8") as f:
        soc_db = json.load(f)

    # Lazy import to avoid cycles when called as standalone
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from data import ontology_loader as ont

    cancer_input = patient.get("cancer_type", "")
    cancer_key = ont.normalize_cancer_key(cancer_input)
    benchmarks = soc_db.get("benchmarks", {}).get(cancer_key, {})

    lines = patient.get("treatment_lines_completed", 0)
    prior_text = " ".join(patient.get("prior_therapies", []))

    # Detect which regimens patient has received (using ontology — no hardcoded chemo names)
    received_regimens = ont.detect_regimens_in_text(prior_text)

    # Pick appropriate SoC bucket
    if lines == 0:
        # Try molecular-subtype-specific 1L key first
        muts = patient.get("mutations", [])
        for mut in muts:
            mut_str = mut.lower().replace(" ", "_").replace(".", "")
            specific_key = f"metastatic_1L_{mut_str}"
            if specific_key in benchmarks:
                return benchmarks[specific_key]
            # Try truncated forms — match by gene OR by variant
            tokens = mut_str.split("_")
            for k in benchmarks:
                if k.startswith("metastatic_1L_") and any(p in k.lower() for p in tokens if len(p) >= 3):
                    return benchmarks[k]
        return benchmarks.get("metastatic_1L", [])
    elif lines == 1:
        # First, try therapy-class keys (e.g., post_egfr_tki) inferred from patient priors
        from data import ontology_loader as ont2
        therapy_classes = ont2.detect_therapy_classes_in_history(patient.get("prior_therapies", []))
        for cls in therapy_classes:
            cls_token = cls.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_").replace("-", "_")
            for k in benchmarks:
                if k.lower().startswith("metastatic_2l_post_") and any(part in k.lower() for part in cls_token.split("_") if len(part) >= 3):
                    return benchmarks[k]

        # Then try regimen-name keys (e.g., post_FOLFIRINOX)
        for regimen in received_regimens:
            key = f"metastatic_2L_post_{regimen.replace(' ', '_').replace('+', '').replace('/', '_')}"
            if key in benchmarks:
                return benchmarks[key]
            for k in benchmarks:
                if k.lower().startswith("metastatic_2l_post_") and regimen.split()[0].lower() in k.lower():
                    return benchmarks[k]
        # Generic 2L fallback
        return benchmarks.get("metastatic_2L", benchmarks.get("metastatic_2L_post_FOLFIRINOX", []))
    elif lines >= 2:
        return benchmarks.get(f"metastatic_{lines+1}L", benchmarks.get("metastatic_3L_plus", []))
    return []


# CLI
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--patient", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    with open(args.patient) as f:
        patient = json.load(f)

    db = load_db()
    soc = get_soc_for(patient)

    def annotate(trials):
        for t in trials:
            if "error" in t:
                continue
            t["efficacy"] = lookup_efficacy(t, db)
        return trials

    if isinstance(data, dict) and "match" in data:
        data["match"] = annotate(data["match"])
        data["conditional"] = annotate(data["conditional"])
    data["soc_benchmarks"] = soc

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    n_with_eff = sum(1 for t in data.get('match', []) + data.get('conditional', []) if t.get('efficacy'))
    print(f"Efficacy annotated. {n_with_eff} trials have efficacy data; {len(soc)} SoC regimens.")
