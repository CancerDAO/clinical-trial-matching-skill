"""
gating.py — v1.6.0 P0.3

Distinguish BLOCKER variables (missing → cannot recommend specific path) from
ADVISOR variables (missing → confidence reduction only).

Output:
  GatingResult(
      eligible: bool,                       # patient passes hard gates
      verdict: "match" | "conditional" | "exclude" | "incomplete",
      blockers_satisfied: list[str],
      blockers_failed: list[str],            # hard mismatches
      blockers_pending: list[str],           # unverified blockers (HLA, biomarker)
      advisors_unknown: list[str],           # missing advisors → confidence -
      reasons: list[str],
      confidence_penalty: float,
  )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import sys
from pathlib import Path

# Allow this module to find the ontology loader regardless of cwd
sys.path.insert(0, str(Path(__file__).parent.parent))
from data import ontology_loader as ont  # noqa: E402


@dataclass
class GatingResult:
    eligible: bool
    verdict: str  # "match" / "conditional" / "exclude" / "incomplete"
    blockers_satisfied: list[str] = field(default_factory=list)
    blockers_failed: list[str] = field(default_factory=list)
    blockers_pending: list[str] = field(default_factory=list)
    advisors_unknown: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence_penalty: float = 0.0  # 0.0 = no penalty, 1.0 = max penalty


# ---------------------------------------------------------------------------
# Helper: normalise mutation strings (case + spacing)
# ---------------------------------------------------------------------------
def _normalize_mutation(s: str) -> str:
    return s.upper().replace(" ", "").replace("-", "").replace("P.", "")


def _patient_mutation_matches(patient_muts: list[str], required: list[str], pan_mutation: bool) -> bool:
    """True if patient has any of the required mutations OR pan-mutation acceptable."""
    if not required:
        return True
    pat_norm = {_normalize_mutation(m) for m in patient_muts}
    req_norm = {_normalize_mutation(m) for m in required}

    # Exact match
    if pat_norm & req_norm:
        return True

    # Pan-RAS / RAS-mutant inclusive
    if pan_mutation:
        for p in pat_norm:
            if p.startswith("KRAS") or p.startswith("HRAS") or p.startswith("NRAS"):
                return True

    # "RAS mutation (any)" — accept any KRAS/HRAS/NRAS
    if any("RASMUTATION" in r for r in req_norm):
        for p in pat_norm:
            if any(p.startswith(prefix) for prefix in ["KRAS", "HRAS", "NRAS"]):
                return True

    return False


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------
def evaluate_gate(trial: dict, patient: dict) -> GatingResult:
    """
    trial: dict from nct_results_v160.json (must include 'metadata' field)
    patient: dict with fields:
        mutations: list[str]                  e.g., ["KRAS G12D"]
        treatment_lines_completed: int        e.g., 1
        disease_stage: str                    e.g., "metastatic" / "locally_advanced" / "resectable"
        ecog: int                             0-4
        hla_typed: bool                       have we typed HLA?
        hla_alleles: list[str]                if typed, list of alleles (e.g., ["HLA-A*11:01"])
        biomarkers_known: dict[str, bool]     e.g., {"CLDN18.2": True, "MTAP": None}
        prior_therapies: list[str]            classes patient has received
        cns_imaging_done: bool                MRI / CT confirmed no CNS mets
        viral_serology_done: bool             HBV / HCV / HIV cleared
        age: int|None
    """
    md = trial.get("metadata", {})
    tlp = md.get("treatment_line_policy", {})
    dsp = md.get("disease_stage_policy", {})
    mr = md.get("mutation_requirements", {})
    prior_excl = md.get("prior_therapy_exclusions", [])
    hla_req = md.get("hla_requirements", [])
    bio_req = md.get("biomarker_requirements", [])
    trial_type = md.get("trial_type", "interventional")

    result = GatingResult(eligible=True, verdict="match")

    # ---------- BLOCKER 0: Trial type ----------
    # Imaging / observational / supportive trials don't apply to therapeutic decision making
    if trial_type in ("imaging", "observational", "supportive", "screening"):
        result.eligible = False
        result.verdict = "exclude"
        result.reasons.append(f"Trial type is '{trial_type}' (not therapeutic interventional)")
        result.blockers_failed.append(f"Trial type: {trial_type}")
        return result

    # ---------- v1.7 BLOCKER 0.5: Cancer-type strict gate ----------
    # Verify that the trial title / interventions / verification.conditions
    # contain the patient cancer type or a wildcard (solid tumor, KRAS mutation, etc.).
    # All cancer-specific knowledge comes from data/clinical_ontology.json (v1.7.1 generalization).
    pat_cancer_input = patient.get("cancer_type") or ""
    pat_muts = [_normalize_mutation(m) for m in patient.get("mutations", [])]

    aliases = ont.get_cancer_aliases(pat_cancer_input)
    anti_aliases = ont.get_cancer_anti_aliases(pat_cancer_input)

    # Combined text from trial: title + interventions + verifier conditions
    cancer_text_sources = [
        trial.get("title", "").lower(),
        " ".join(trial.get("interventions", [])).lower(),
        " ".join(trial.get("verification", {}).get("conditions", [])).lower(),
    ]
    cancer_text = " ".join(cancer_text_sources)

    wildcards = ["solid tumor", "tumor, solid", "solid cancer", "advanced cancer", "advanced solid",
                  "all solid", "pan-tumor", "pan tumor", "any cancer", "metastatic cancer"]
    mutation_indicators = ["kras g12d", "kras g12c", "kras g12v", "ras mutant", "ras-mutated",
                            "ras mutation", "her2 positive", "egfr mutation", "msi-h", "dmmr"]
    pat_mut_in_text = any(m.replace(" ", "").lower() in cancer_text.replace(" ", "")
                           or any(p.lower() in cancer_text for p in m.split())
                           for m in patient.get("mutations", []))

    if aliases:
        # Token-level: each verification.conditions item must NOT be purely an anti-alias
        verif_conds = trial.get("verification", {}).get("conditions", [])
        cancer_match_strict = False
        for c in verif_conds:
            c_lower = c.lower()
            if any(a in c_lower for a in aliases) and not any(aa in c_lower for aa in anti_aliases):
                cancer_match_strict = True
                break

        # Fallback to title text if verifier didn't run yet
        if not cancer_match_strict and not verif_conds:
            for src in cancer_text_sources:
                if any(a in src for a in aliases) and not any(aa in src for aa in anti_aliases):
                    cancer_match_strict = True
                    break

        wildcard_match = any(w in cancer_text for w in wildcards)
        # Anti-alias guard on wildcard: if all wildcards in trial are co-occurring with anti-aliases, reject
        if wildcard_match and verif_conds:
            wildcard_clean = any(
                any(w in c.lower() for w in wildcards)
                and not any(aa in c.lower() for aa in anti_aliases)
                for c in verif_conds
            )
            if not wildcard_clean:
                wildcard_match = False
        mutation_match = any(mi in cancer_text for mi in mutation_indicators) and pat_mut_in_text

        if not (cancer_match_strict or wildcard_match or mutation_match):
            result.eligible = False
            result.verdict = "exclude"
            result.reasons.append(
                f"Cancer-type strict gate: trial conditions do not include patient cancer ({pat_cancer_input}) "
                f"or any pan-tumor/mutation-defined wildcard."
            )
            result.blockers_failed.append("cancer_type_strict_gate")
            return result

    # ---------- BLOCKER 1: Mutation match ----------
    pat_muts = patient.get("mutations", [])
    pan = mr.get("pan_mutation_acceptable", False)
    if mr.get("required"):
        if _patient_mutation_matches(pat_muts, mr["required"], pan):
            result.blockers_satisfied.append(f"Mutation match ({mr['required']})")
        else:
            result.blockers_failed.append(f"Mutation mismatch: trial requires {mr['required']}; patient has {pat_muts}")
            result.eligible = False
            result.verdict = "exclude"
            result.reasons.append(f"Trial requires {mr['required']} but patient has {pat_muts}")

    # ---------- BLOCKER 2: Treatment line policy ----------
    pat_lines = patient.get("treatment_lines_completed", 0)

    if tlp.get("treatment_naive_required"):
        if pat_lines == 0:
            result.blockers_satisfied.append("Treatment-naive (matches)")
        else:
            result.blockers_failed.append(f"Trial requires treatment-naive; patient has {pat_lines}L")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"Patient already received {pat_lines} prior line(s); trial requires treatment-naive")

    if tlp.get("min_prior_lines", 0) > 0:
        if pat_lines >= tlp["min_prior_lines"]:
            result.blockers_satisfied.append(f"≥{tlp['min_prior_lines']} prior lines (patient has {pat_lines})")
        else:
            result.blockers_failed.append(f"Trial requires ≥{tlp['min_prior_lines']} prior lines; patient has {pat_lines}")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"Patient has {pat_lines}L; trial requires ≥{tlp['min_prior_lines']}L")

    if tlp.get("max_prior_lines") is not None:
        max_lines = tlp["max_prior_lines"]
        if pat_lines <= max_lines:
            result.blockers_satisfied.append(f"≤{max_lines} prior lines (patient has {pat_lines})")
        else:
            result.blockers_failed.append(f"Trial limits to ≤{max_lines} prior lines; patient has {pat_lines}")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"Patient has {pat_lines}L; trial allows ≤{max_lines}L")

    # ---------- BLOCKER 3: Disease stage ----------
    pat_stage = patient.get("disease_stage", "metastatic")
    setting = dsp.get("setting", "mixed")

    if setting in ("neoadjuvant", "adjuvant", "conversion"):
        # patient must have resectable / locally advanced disease for these settings
        if pat_stage in ("metastatic",):
            result.blockers_failed.append(f"Trial setting={setting}; patient is {pat_stage}")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"Trial requires {setting} setting (resectable/locally advanced); patient is {pat_stage}")
        else:
            result.blockers_satisfied.append(f"Stage compatible with {setting} setting")
    elif setting == "metastatic":
        if dsp.get("requires_metastatic") and pat_stage != "metastatic":
            result.blockers_failed.append(f"Trial requires metastatic; patient is {pat_stage}")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append("Patient stage does not meet metastatic requirement")
        else:
            result.blockers_satisfied.append("Stage acceptable")

    # ---------- BLOCKER 4: HLA (TCR-T / vaccine trials) ----------
    if hla_req:
        if not patient.get("hla_typed", False):
            result.blockers_pending.append(f"HLA typing required ({hla_req})")
            result.verdict = "conditional" if result.eligible else result.verdict
            result.reasons.append(f"HLA typing not yet performed; trial requires one of {hla_req}")
        else:
            patient_hla = patient.get("hla_alleles", [])
            # Patient HLA must include AT LEAST ONE of the required alleles
            req_match = False
            for hr in hla_req:
                # Allele match — exact or partial (HLA-A*11 covers HLA-A*11:01)
                for ph in patient_hla:
                    if ph.startswith(hr) or hr.startswith(ph):
                        req_match = True
                        break
                if req_match:
                    break
            if req_match:
                result.blockers_satisfied.append(f"HLA match ({patient_hla} ∩ {hla_req})")
            else:
                result.blockers_failed.append(f"HLA mismatch: trial needs {hla_req}, patient has {patient_hla}")
                if result.eligible:
                    result.eligible = False
                    result.verdict = "exclude"
                    result.reasons.append(f"Patient HLA {patient_hla} does not match required {hla_req}")

    # ---------- BLOCKER 5: Biomarker (CLDN18.2 / MSLN / MTAP) ----------
    pat_bio = patient.get("biomarkers_known", {})
    for bio in bio_req:
        bio_norm = bio.upper()
        # Match against known biomarkers (allow partial: "CLDN18.2" vs "CLDN18")
        known_value = None
        for pk, pv in pat_bio.items():
            if pk.upper().startswith(bio_norm[:4]) or bio_norm.startswith(pk.upper()[:4]):
                known_value = pv
                break

        if known_value is None:
            result.blockers_pending.append(f"{bio} status unknown")
            result.verdict = "conditional" if result.eligible else result.verdict
            result.reasons.append(f"{bio} biomarker testing pending")
        elif known_value is False:
            result.blockers_failed.append(f"{bio} negative (trial requires positive)")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"{bio} negative; trial requires positive")
        else:
            result.blockers_satisfied.append(f"{bio} positive (matches)")

    # ---------- BLOCKER 6: Prior therapy exclusions ----------
    # v1.7.1 generalization: route through ontology therapy_class lookup. Each
    # exclusion label is fuzzy-matched against ontology class aliases, then we
    # check if patient.prior_therapies contain any drug from that class.
    pat_priors = patient.get("prior_therapies", [])
    excl_label_to_class = {
        # metadata extractor labels → ontology class names
        "prior_kras_g12d_inhibitor": "KRAS G12D inhibitor",
        "prior_investigational_kras_g12d": "KRAS G12D inhibitor",
        "prior_kras_inhibitor": "KRAS G12D inhibitor",  # generic prior-KRAS catches G12D too
        "prior_kras_targeted": "KRAS G12D inhibitor",
        "prior_ras_targeted_therapy": "Pan-RAS / Multi-RAS inhibitor",
        "prior_egfr_targeted_therapy": "EGFR antibody",  # also EGFR-TKI; checked below
        "prior_checkpoint_inhibitor": "Immune Checkpoint Inhibitor (ICI)",
        "prior_prmt5_or_mat2a_inhibitor": "PRMT5 inhibitor",
    }
    for excl in prior_excl:
        excl_label = excl.lower()
        triggered_classes = []

        # 1. Direct mapping
        if excl_label in excl_label_to_class:
            class_label = excl_label_to_class[excl_label]
            if ont.therapy_class_match(class_label, pat_priors):
                triggered_classes.append(class_label)

        # 2. EGFR exclusion: check both EGFR antibody AND EGFR-TKI
        if "egfr" in excl_label:
            for cls in ("EGFR antibody", "EGFR-TKI"):
                if ont.therapy_class_match(cls, pat_priors) and cls not in triggered_classes:
                    triggered_classes.append(cls)

        # 3. Generic KRAS exclusion: also check pan-RAS
        if "kras" in excl_label or "ras" in excl_label:
            if ont.therapy_class_match("Pan-RAS / Multi-RAS inhibitor", pat_priors):
                if "Pan-RAS / Multi-RAS inhibitor" not in triggered_classes:
                    triggered_classes.append("Pan-RAS / Multi-RAS inhibitor")

        if triggered_classes:
            result.blockers_failed.append(f"Triggered prior therapy exclusion: {excl} (patient has class: {triggered_classes})")
            if result.eligible:
                result.eligible = False
                result.verdict = "exclude"
                result.reasons.append(f"Patient previously received therapy class blocked by trial: {triggered_classes}")
        else:
            result.blockers_satisfied.append(f"No conflict with prior-therapy exclusion: {excl}")

    # ---------- ADVISOR 1: ECOG ----------
    pat_ecog = patient.get("ecog", 1)
    if pat_ecog > 1:
        result.advisors_unknown.append(f"ECOG {pat_ecog} (most trials require ≤1)")
        result.confidence_penalty += 0.10

    # ---------- ADVISOR 2: CNS imaging ----------
    if not patient.get("cns_imaging_done", False):
        result.advisors_unknown.append("CNS imaging (MRI) not documented")
        result.confidence_penalty += 0.10

    # ---------- ADVISOR 3: Viral serology ----------
    if not patient.get("viral_serology_done", False):
        result.advisors_unknown.append("HBV/HCV/HIV serology not documented")
        result.confidence_penalty += 0.05

    # ---------- ADVISOR 4: Age ----------
    if patient.get("age") is None:
        result.advisors_unknown.append("Age not documented")
        result.confidence_penalty += 0.05

    # Cap penalty at 0.5
    result.confidence_penalty = min(result.confidence_penalty, 0.5)

    # Final verdict adjustment
    if result.eligible and result.blockers_pending:
        # at least 2 pending blockers → conditional
        if len(result.blockers_pending) >= 2 or any("HLA" in b for b in result.blockers_pending):
            result.verdict = "conditional"
        else:
            # single non-HLA pending = still incomplete
            result.verdict = "conditional"

    # If many advisors unknown but no blockers — still match but with penalty
    if result.eligible and not result.blockers_pending and not result.blockers_failed:
        result.verdict = "match"

    return result


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------
def gate_all_trials(trials: list[dict], patient: dict) -> dict:
    """
    Return:
      {
        "match":       [...trials that pass all blockers],
        "conditional": [...trials with pending blockers],
        "exclude":     [...trials with failed blockers],
      }
    """
    buckets = {"match": [], "conditional": [], "exclude": []}
    for t in trials:
        if "error" in t:
            continue
        if "metadata" not in t:
            # unannotated — treat as conditional
            t["gating"] = {"verdict": "conditional", "reasons": ["No metadata extracted"]}
            buckets["conditional"].append(t)
            continue
        gr = evaluate_gate(t, patient)
        t["gating"] = {
            "eligible": gr.eligible,
            "verdict": gr.verdict,
            "blockers_satisfied": gr.blockers_satisfied,
            "blockers_failed": gr.blockers_failed,
            "blockers_pending": gr.blockers_pending,
            "advisors_unknown": gr.advisors_unknown,
            "reasons": gr.reasons,
            "confidence_penalty": gr.confidence_penalty,
        }
        buckets[gr.verdict if gr.verdict in buckets else "exclude"].append(t)
    return buckets


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--patient", required=True, help="patient profile JSON file")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    with open(args.patient) as f:
        patient = json.load(f)

    trials = data.get("included_trials", data) if isinstance(data, dict) else data
    buckets = gate_all_trials(trials, patient)
    out = {
        "match": buckets["match"],
        "conditional": buckets["conditional"],
        "exclude": buckets["exclude"],
        "summary": {
            "match": len(buckets["match"]),
            "conditional": len(buckets["conditional"]),
            "exclude": len(buckets["exclude"]),
            "total": sum(len(b) for b in buckets.values()),
        },
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    s = out["summary"]
    print(f"Gating complete: match={s['match']} conditional={s['conditional']} exclude={s['exclude']} (total={s['total']})")
