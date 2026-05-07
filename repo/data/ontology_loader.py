"""
ontology_loader.py — v1.7.1

Single source of truth for clinical ontology lookups. All modules (gating,
synthesizer, decision_paths) call into here instead of hard-coding.

Generalization principle: code is mechanism, data is knowledge.
Adding a new cancer = add an entry to data/clinical_ontology.json. No code change.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).parent / "clinical_ontology.json"


@lru_cache(maxsize=1)
def load(path: str = None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Cancer alias / anti-alias lookups
# ---------------------------------------------------------------------------
def normalize_cancer_key(cancer_name: str) -> str:
    """
    Map any cancer name (free-form input) to its canonical key.
    Returns "_default" if unrecognized.
    """
    if not cancer_name:
        return "_default"
    name = cancer_name.strip().upper()
    ont = load()
    cancers = ont.get("cancers", {})

    # Direct key match
    if name in cancers:
        return name
    # Lowercase name in aliases or full_names
    name_lower = cancer_name.strip().lower()
    for key, info in cancers.items():
        if key == "_default":
            continue
        full_names = [n.lower() for n in info.get("full_names", [])]
        aliases = [a.lower() for a in info.get("aliases", [])]
        if name_lower in full_names or name_lower in aliases:
            return key
    return "_default"


def get_cancer_aliases(cancer_name: str) -> list[str]:
    """All synonyms for the cancer (for matching trial conditions / titles)."""
    key = normalize_cancer_key(cancer_name)
    ont = load()
    info = ont.get("cancers", {}).get(key, {})
    aliases = info.get("aliases", []) + [n.lower() for n in info.get("full_names", [])]
    if key != "_default":
        aliases.append(key.lower())
    return list(set(aliases))


def get_cancer_anti_aliases(cancer_name: str) -> list[str]:
    """Histologic distinctions to exclude (e.g., PDAC vs pancreatic NET)."""
    key = normalize_cancer_key(cancer_name)
    ont = load()
    info = ont.get("cancers", {}).get(key, {})
    return info.get("anti_aliases", [])


def get_median_os_at_line(cancer_name: str, lines_completed: int,
                          molecular_subtype: Optional[str] = None) -> Optional[float]:
    """
    Look up published median OS at the patient's current line.
    Falls back through subtype → generic line key → _default.
    """
    key = normalize_cancer_key(cancer_name)
    ont = load()
    info = ont.get("cancers", {}).get(key, {})
    table = info.get("median_os_months_at_line", {})

    # Try subtype-specific (e.g., "0_naive_metastatic_egfr_mut") first
    if lines_completed == 0 and molecular_subtype:
        candidates = [k for k in table if k.startswith("0_naive_metastatic_") and molecular_subtype.lower() in k.lower()]
        if candidates:
            return float(table[candidates[0]])

    # Generic line keys
    line_key = {
        0: "0_naive_metastatic",
        1: "1_post_first_line",
        2: "2_post_second_line",
    }.get(lines_completed, "3_plus" if lines_completed >= 3 else None)

    if line_key and line_key in table:
        return float(table[line_key])
    if "_default_unknown_threshold" in table:
        return float(table["_default_unknown_threshold"])
    return None


def get_goc_threshold(cancer_name: str) -> float:
    """Median-OS threshold (months) below which Goals of Care discussion is triggered."""
    key = normalize_cancer_key(cancer_name)
    ont = load()
    info = ont.get("cancers", {}).get(key, {})
    return float(info.get("goc_trigger_threshold_months", 6.0))


def get_rapid_progression_threshold(cancer_name: str) -> int:
    """Number-of-cycles below which '1L PD' counts as rapid progression."""
    key = normalize_cancer_key(cancer_name)
    ont = load()
    info = ont.get("cancers", {}).get(key, {})
    return int(info.get("rapid_progression_cycles_threshold", 4))


# ---------------------------------------------------------------------------
# Chemo regimen lookup (for backbone overlap detection)
# ---------------------------------------------------------------------------
def get_all_chemo_regimens() -> dict:
    """Return the full chemo_regimens map (excluding _doc-prefixed keys)."""
    raw = load().get("chemo_regimens", {})
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


def detect_regimens_in_text(text: str) -> list[str]:
    """
    Return list of regimen names whose components or aliases appear in `text`.
    Use for: (a) detecting trial backbone, (b) detecting patient prior therapy.
    """
    text_lower = text.lower()
    matched = []
    for regimen_name, info in get_all_chemo_regimens().items():
        components = info.get("components", []) + info.get("aliases", [])
        # Match if ANY component or alias is in text
        if any(c.lower() in text_lower for c in components):
            matched.append(regimen_name)
    return matched


def find_chemo_overlap(trial_text: str, patient_prior_text: str,
                        min_component_overlap: int = 1) -> list[dict]:
    """
    Detect overlap between trial chemo backbone and patient prior failed therapy.
    Returns list of overlapping regimens with the matching components.
    """
    trial_text_lower = trial_text.lower()
    patient_text_lower = patient_prior_text.lower()

    overlaps = []
    for regimen_name, info in get_all_chemo_regimens().items():
        components = info.get("components", [])
        aliases = info.get("aliases", [])
        all_keys = components + aliases

        trial_hits = [k for k in all_keys if k.lower() in trial_text_lower]
        patient_hits = [k for k in all_keys if k.lower() in patient_text_lower]

        if trial_hits and patient_hits:
            # require at least 1 component hit on patient side (not just regimen-name alias)
            patient_component_hits = [k for k in patient_hits if k in components]
            if patient_component_hits:
                overlaps.append({
                    "regimen": regimen_name,
                    "components_in_trial": trial_hits,
                    "components_in_patient_history": patient_hits,
                })
    return overlaps


# ---------------------------------------------------------------------------
# Prior therapy class lookup
# ---------------------------------------------------------------------------
def get_all_therapy_classes() -> dict:
    raw = load().get("prior_therapy_classes", {})
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


def detect_therapy_classes_in_history(prior_therapies: list[str]) -> list[str]:
    """
    Given patient.prior_therapies (list of drug names),
    return list of therapy CLASSES the patient has received.
    """
    text = " ".join(prior_therapies).lower()
    matched = []
    for class_name, info in get_all_therapy_classes().items():
        components = info.get("components", []) + info.get("aliases", [])
        if any(c.lower() in text for c in components):
            matched.append(class_name)
    return matched


def therapy_class_match(class_label: str, patient_prior: list[str]) -> bool:
    """
    Did patient receive any drug from a given therapy class?
    Used by gating to evaluate prior_therapy_exclusions.
    """
    classes = get_all_therapy_classes()
    info = classes.get(class_label, {})
    if not info:
        # try fuzzy: match against aliases
        for k, v in classes.items():
            if class_label.lower() in [a.lower() for a in v.get("aliases", []) + [k]]:
                info = v
                break
    if not info:
        return False
    text = " ".join(patient_prior).lower()
    return any(c.lower() in text for c in info.get("components", []) + info.get("aliases", []))


# ---------------------------------------------------------------------------
# Biomarker alias lookup
# ---------------------------------------------------------------------------
def get_biomarker_aliases() -> dict:
    raw = load().get("biomarker_aliases", {})
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)}


def detect_biomarkers_in_text(text: str) -> list[str]:
    text_lower = text.lower()
    matched = []
    for canonical, aliases in get_biomarker_aliases().items():
        if any(a.lower() in text_lower for a in aliases):
            matched.append(canonical)
    return matched


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # PDAC tests
    assert normalize_cancer_key("PDAC") == "PDAC"
    assert normalize_cancer_key("Pancreatic Ductal Adenocarcinoma") == "PDAC"
    assert normalize_cancer_key("pancreatic adenocarcinoma") == "PDAC"
    assert "pancreatic" in get_cancer_aliases("PDAC")
    assert "neuroendocrine" in get_cancer_anti_aliases("PDAC")
    assert get_goc_threshold("PDAC") == 6.0
    assert get_median_os_at_line("PDAC", 1) == 6.0

    # NSCLC tests
    assert normalize_cancer_key("NSCLC") == "NSCLC"
    assert normalize_cancer_key("Non-Small Cell Lung Cancer") == "NSCLC"
    assert "nsclc" in get_cancer_aliases("NSCLC")
    assert "sclc" in get_cancer_anti_aliases("NSCLC")
    assert get_median_os_at_line("NSCLC", 0, "egfr_mut") == 38.0

    # CRC tests
    assert normalize_cancer_key("colorectal") == "CRC"

    # Unknown cancer fallback
    assert normalize_cancer_key("breast") == "_default"
    assert get_goc_threshold("breast") == 6.0  # default

    # Chemo overlap detection
    overlaps = find_chemo_overlap(
        "treatment with FOLFIRINOX or NALIRIFOX",
        "patient received gemcitabine, oxaliplatin"
    )
    assert any(o["regimen"] == "FOLFIRINOX" for o in overlaps), f"Expected FOLFIRINOX, got {overlaps}"

    # Prior therapy class
    assert therapy_class_match("EGFR-TKI", ["osimertinib"])
    assert therapy_class_match("EGFR antibody", ["cetuximab"])
    assert therapy_class_match("KRAS G12D inhibitor", ["MRTX1133"])
    assert not therapy_class_match("ALK inhibitor", ["osimertinib"])

    print("✅ ontology_loader self-test passed")
    print(f"\n{normalize_cancer_key('NSCLC')} aliases: {get_cancer_aliases('NSCLC')}")
    print(f"NSCLC GoC threshold (1L EGFR-mut): {get_median_os_at_line('NSCLC', 0, 'egfr_mut')} months")
    print(f"Detected EGFR-TKI from patient history ['osimertinib', 'crizotinib']: "
          f"{detect_therapy_classes_in_history(['osimertinib', 'crizotinib'])}")
