"""
risk_lookup.py — v1.6.0 P1.3

Match a trial × patient against the risk_taxonomy.json catalog and produce
a list of applicable risk profiles with notes.

Used by Decision Report synthesizer to populate the "Risks" field for each path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_TAXONOMY_PATH = Path(__file__).parent.parent / "data" / "risk_taxonomy.json"


_taxonomy_cache = None


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY_PATH) -> dict:
    global _taxonomy_cache
    if _taxonomy_cache is None:
        with open(path, "r", encoding="utf-8") as f:
            _taxonomy_cache = json.load(f)
    return _taxonomy_cache


def lookup_risks(trial: dict, patient: dict, taxonomy: dict | None = None) -> list[dict]:
    """Return list of applicable risk profiles."""
    if taxonomy is None:
        taxonomy = load_taxonomy()

    title = trial.get("title", "").lower()
    interventions = " ".join(trial.get("interventions", [])).lower()
    phases = trial.get("phases", []) or trial.get("verification", {}).get("phases_official", [])
    cn_sites = trial.get("china_site_count", 0)
    pat_country = patient.get("country", "")
    pat_cancer = patient.get("cancer_type", "").lower()

    text = title + " " + interventions
    # cancer keywords match against patient cancer (not trial title) — risk is patient-specific
    pat_cancer_aliases = {
        "pdac": ["pancreatic", "pdac"],
        "pancreatic ductal adenocarcinoma": ["pancreatic", "pdac"],
        "nsclc": ["nsclc", "non-small cell lung", "lung"],
        "crc": ["crc", "colorectal", "colon"],
    }.get(pat_cancer, [pat_cancer])
    pat_cancer_text = " ".join(pat_cancer_aliases)

    applicable = []
    for prof in taxonomy.get("risk_profiles", []):
        # Check each component independently
        mechanism_kws = (prof.get("title_keywords", []) +
                         prof.get("intervention_keywords", []) +
                         prof.get("drug_keywords", []))
        cancer_kws = prof.get("cancer_keywords", [])
        phase_kws = prof.get("phase_keywords", [])

        mechanism_hit = any(kw.lower() in text for kw in mechanism_kws) if mechanism_kws else None
        # cancer match: against patient cancer aliases OR against "any_solid" wildcard
        if cancer_kws:
            if "any_solid" in [c.lower() for c in cancer_kws] or "any" in [c.lower() for c in cancer_kws]:
                cancer_hit = True
            else:
                cancer_hit = any(kw.lower() in pat_cancer_text for kw in cancer_kws)
        else:
            cancer_hit = None
        # also support "cancer" field directly: "PDAC" / "any_solid" etc.
        cancer_field = prof.get("cancer", "").lower()
        if cancer_field:
            if "any" in cancer_field:
                cancer_hit = True
            elif "|" in cancer_field:  # multi-cancer e.g., "PDAC|gastric"
                cancers = cancer_field.split("|")
                if any(c.strip() in pat_cancer_text or pat_cancer in c.strip() for c in cancers):
                    cancer_hit = True
                else:
                    cancer_hit = False if cancer_hit is None else cancer_hit
            elif cancer_field in pat_cancer_text or pat_cancer in cancer_field:
                cancer_hit = True
            else:
                cancer_hit = False if cancer_hit is None else cancer_hit
        phase_hit = False
        if phase_kws:
            for ph in phases:
                if ph in phase_kws:
                    if ph == "PHASE1" and "PHASE2" not in phases:
                        phase_hit = True
                    break

        # Resolution rule:
        # - If profile defines BOTH mechanism and cancer keywords → require BOTH
        # - If profile defines ONLY mechanism → just mechanism
        # - If profile defines ONLY cancer → just cancer
        # - If profile defines ONLY phase → just phase
        # - Special case: overseas
        matched = False
        if mechanism_kws and cancer_kws:
            matched = bool(mechanism_hit and cancer_hit)
        elif mechanism_kws:
            matched = bool(mechanism_hit)
        elif cancer_kws and not phase_kws:
            matched = bool(cancer_hit)
        elif phase_kws:
            matched = phase_hit

        # Overseas detection (overrides — independent of keyword logic)
        if prof["key"] == "overseas_us_eu":
            applies_country = prof.get("applies_to_country", [])
            if pat_country in applies_country and cn_sites == 0:
                matched = True

        if matched:
            applicable.append({
                "key": prof["key"],
                "mechanism": prof["mechanism"],
                "risk_level": prof["risk_level"],
                "notes": prof["notes"],
            })

    return applicable


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

    def annotate_bucket(trials):
        for t in trials:
            if "error" in t:
                continue
            t["risk_profiles"] = lookup_risks(t, patient)
        return trials

    if isinstance(data, dict) and "match" in data:
        data["match"] = annotate_bucket(data["match"])
        data["conditional"] = annotate_bucket(data["conditional"])

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Risk profiles annotated.")
