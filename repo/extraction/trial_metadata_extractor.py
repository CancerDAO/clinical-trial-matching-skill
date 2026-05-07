"""
trial_metadata_extractor.py — v1.6.0

Replaces v1.5.0 keyword-based `line_info` heuristic with structured extraction:
  - treatment_line_policy: {treatment_naive_required, min_prior_lines, max_prior_lines, evidence}
  - disease_stage_policy:  {requires_metastatic, accepts_*, setting, evidence}
  - mutation_requirements: {required, excluded, pan_acceptable, evidence}
  - prior_therapy_exclusions: list of drug classes excluded
  - hla_requirements:      list of required HLA alleles
  - biomarker_requirements: list of required biomarkers (CLDN18.2, MSLN, MTAP, ...)

Each extraction produces evidence quotes (raw text + source field) for citation chain.
Heuristic-first; the LLM (Claude in skill flow) can override / refine via the
`augment_with_llm_extraction()` helper.

CLI:
    python -m extraction.trial_metadata_extractor \\
        --in nct_results.json --out nct_results_with_metadata.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    quote: str
    field: str  # which CT.gov field the quote came from
    char_offset: int = -1


@dataclass
class TreatmentLinePolicy:
    treatment_naive_required: bool = False
    min_prior_lines: int = 0
    max_prior_lines: Optional[int] = None
    accepts_post_progression: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    confidence: str = "medium"  # low / medium / high


@dataclass
class DiseaseStagePolicy:
    requires_metastatic: bool = False
    accepts_metastatic: bool = True
    accepts_locally_advanced: bool = True
    accepts_borderline_resectable: bool = False
    accepts_resectable: bool = False
    accepts_recurrent: bool = False
    setting: str = "mixed"  # neoadjuvant / adjuvant / conversion / metastatic / mixed
    evidence: list[Evidence] = field(default_factory=list)
    confidence: str = "medium"


@dataclass
class MutationRequirements:
    required: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    pan_mutation_acceptable: bool = False
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class TrialMetadata:
    treatment_line_policy: TreatmentLinePolicy = field(default_factory=TreatmentLinePolicy)
    disease_stage_policy: DiseaseStagePolicy = field(default_factory=DiseaseStagePolicy)
    mutation_requirements: MutationRequirements = field(default_factory=MutationRequirements)
    prior_therapy_exclusions: list[str] = field(default_factory=list)
    hla_requirements: list[str] = field(default_factory=list)
    biomarker_requirements: list[str] = field(default_factory=list)
    trial_type: str = "interventional"  # interventional / imaging / observational / screening / supportive
    extraction_method: str = "heuristic_v1"  # heuristic_v1 / llm_assisted / hybrid


# Trial-type detection patterns
IMAGING_OBSERVATIONAL_PATTERNS = [
    (r"\b(?:immunopet|pet[/ ]?ct\b.*imaging|imaging.*pet|fluorescence imaging|optical imaging)\b", "imaging"),
    (r"\b(?:imaging study|imaging trial|imaging biomarker|radiotracer)\b", "imaging"),
    (r"\b(?:screening study|screening trial|early detection|biomarker discovery)\b", "screening"),
    (r"\b(?:registry|natural history|observational study|cohort study|surveillance)\b", "observational"),
    (r"\b(?:pain control|symptom management|supportive care|palliative care|fatigue|cachexia|nutrition)\b(?!.*tumor)", "supportive"),
    (r"\b(?:nursing|care quality|education|psychotherapy|psychosocial|caregiver)\b", "supportive"),
    (r"\b(?:nerve block|anesthesia|analgesia|hypotension intraoperative)\b", "supportive"),
    (r"\bdeep learning|machine learning|AI[ -]model\b.*(?:diagnosis|detection|classification)", "screening"),
]


def detect_trial_type(title: str, interventions: list[str], elig: str = "") -> str:
    """Classify trial as interventional vs imaging/observational/supportive."""
    text = (title + " " + " ".join(interventions) + " " + elig[:500]).lower()
    for pat, label in IMAGING_OBSERVATIONAL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            # Don't mis-classify therapeutic trials that mention imaging as endpoint
            if label == "imaging" and any(k in text for k in ["chemotherapy", "targeted therapy", "drug arm", "treatment arm"]):
                continue
            return label
    return "interventional"


# ---------------------------------------------------------------------------
# Helpers — evidence-aware regex extraction
# ---------------------------------------------------------------------------
def _find_first(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[re.Match]:
    return re.search(pattern, text, flags)


def _make_evidence(match: re.Match, field_name: str = "eligibilityCriteria",
                   pad: int = 80) -> Evidence:
    text = match.string
    start = max(0, match.start() - pad)
    end = min(len(text), match.end() + pad)
    quote = text[start:end].strip().replace("\n", " ")
    return Evidence(quote=quote, field=field_name, char_offset=match.start())


# ---------------------------------------------------------------------------
# 1. Treatment line policy
# ---------------------------------------------------------------------------
LINE_NAIVE_PATTERNS = [
    # explicit naïve / untreated
    r"treatment[- ]naïve\b",
    r"treatment[- ]naive\b",
    r"\btreatment[- ]?na[iï]ve\b",
    r"\bchemo[- ]?na[iï]ve\b",
    r"\bsystemic therapy[- ]?na[iï]ve\b",
    r"no prior systemic (?:antitumor |anti[- ]?cancer |chemo)?(?:therapy|treatment)",
    r"not received (?:any )?(?:prior )?systemic",
    r"previously untreated",
    r"have not received (?:any )?(?:prior )?(?:systemic |chemo)?(?:therapy|treatment)",
    # explicit first-line wording (in inclusion / population context)
    r"\bfirst[- ]line(?:\s+treatment| therapy| setting)?\b(?:\s+of)?",
    r"\b1L\s+(?:treatment|setting|patients|subjects)",
    r"as (?:the )?first[- ]line",
]

LINE_2L_PLUS_PATTERNS = [
    r"having failed (?:or intolerant to )?at least (?:one|two|three|\d+) lines? of",
    r"failed (?:at least )?(?:one|two|three|\d+) (?:lines? of )?(?:prior )?(?:standard )?(?:therapy|treatment|chemo)",
    r"progressed (?:on|after) (?:at least )?(?:one|two|three|\d+) (?:lines? of )?(?:prior )?(?:standard )?(?:therapy|treatment)",
    r"progressed following (?:at least )?(?:one|two|three|\d+) lines?",
    r"≥\s*\d+ (?:lines? of )?prior",
    r"at least\s+(?:one|two|three|\d+)\s+lines?",
    r"\bsecond[- ]line\b",
    r"\bthird[- ]line\b",
    r"prior standard (?:of[- ]care|therapy)",
    r"\b2L\b",
    r"\b3L\b",
    r"prior (?:systemic )?(?:treatment|therapy) (?:has|have) failed",
    r"refractory to (?:standard )?(?:therapy|treatment)",
    r"intoleran(?:t|ce) to (?:standard|prior)",
    r"recurrent (?:or|/)\s*metastatic",  # often implies post-treatment
    r"received and progressed",
    r"prior treatment of (?:the patient'?s )?(?:tumor|disease)",
]

# Word -> integer mapping for "two", "three"
WORD_TO_INT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

LINE_NUMBER_PATTERNS = [
    (r"≥\s*(\d+) (?:lines?|prior)", "min"),
    (r"at least\s+(\d+)\s+(?:lines? of )?prior", "min"),
    (r"(?:up to|maximum of|no more than)\s+(\d+)\s+(?:lines?|prior)", "max"),
    (r"(\d+)[- ]?(?:to|–|-)[- ]?(\d+)\s+(?:lines?|prior)", "range"),
]


def extract_treatment_line_policy(elig_text: str, title: str = "") -> TreatmentLinePolicy:
    policy = TreatmentLinePolicy()

    if not elig_text:
        policy.confidence = "low"
        return policy

    # 1a. Treatment-naive / first-line patterns
    naive_hit = None
    for pat in LINE_NAIVE_PATTERNS:
        m = _find_first(pat, elig_text)
        if m:
            naive_hit = m
            break

    # 1b. 2L+ patterns
    second_line_hit = None
    for pat in LINE_2L_PLUS_PATTERNS:
        m = _find_first(pat, elig_text)
        if m:
            second_line_hit = m
            break

    # 1c. Title-based hints (last resort)
    title_lower = title.lower()
    title_naive_hit = bool(re.search(r"first[- ]line|untreated|treatment[- ]?na[iï]ve|1L", title_lower))
    title_2l_hit = bool(re.search(r"second[- ]line|previously treated|refractory|recurrent (?:or |/)?metastatic|2L|3L", title_lower))

    # Resolution logic — priority: explicit eligibility text > title
    if naive_hit and not second_line_hit:
        policy.treatment_naive_required = True
        policy.min_prior_lines = 0
        policy.max_prior_lines = 0
        policy.evidence.append(_make_evidence(naive_hit))
        policy.confidence = "high"
    elif second_line_hit and not naive_hit:
        policy.accepts_post_progression = True
        policy.min_prior_lines = 1
        policy.max_prior_lines = None
        policy.evidence.append(_make_evidence(second_line_hit))
        policy.confidence = "high"
    elif naive_hit and second_line_hit:
        # both present — likely 1L primary + previously-treated cohort, or
        # 1L allowed if "previous adjuvant >6 months ago"
        # default to RESTRICTIVE: treat as 1L unless explicit "previously treated allowed"
        policy.treatment_naive_required = True
        policy.evidence.append(_make_evidence(naive_hit))
        policy.evidence.append(_make_evidence(second_line_hit))
        policy.confidence = "low"
    elif title_naive_hit and not title_2l_hit:
        policy.treatment_naive_required = True
        policy.evidence.append(Evidence(quote=title, field="briefTitle", char_offset=0))
        policy.confidence = "medium"
    elif title_2l_hit and not title_naive_hit:
        policy.accepts_post_progression = True
        policy.min_prior_lines = 1
        policy.evidence.append(Evidence(quote=title, field="briefTitle", char_offset=0))
        policy.confidence = "medium"
    else:
        policy.confidence = "low"

    # 1d. Explicit numeric bounds (override min/max)
    for pat, kind in LINE_NUMBER_PATTERNS:
        m = _find_first(pat, elig_text)
        if not m:
            continue
        if kind == "min":
            try:
                policy.min_prior_lines = int(m.group(1))
                policy.evidence.append(_make_evidence(m))
            except (ValueError, IndexError):
                pass
        elif kind == "max":
            try:
                policy.max_prior_lines = int(m.group(1))
                policy.evidence.append(_make_evidence(m))
            except (ValueError, IndexError):
                pass
        elif kind == "range":
            try:
                policy.min_prior_lines = int(m.group(1))
                policy.max_prior_lines = int(m.group(2))
                policy.evidence.append(_make_evidence(m))
            except (ValueError, IndexError):
                pass

    # 1e. "≥2 lines" / "at least two lines" word-form numeric extraction
    word_min = re.search(r"(?:at least|having failed)\s+(\w+)\s+lines?", elig_text, re.IGNORECASE)
    if word_min:
        word = word_min.group(1).lower()
        if word in WORD_TO_INT:
            policy.min_prior_lines = max(policy.min_prior_lines, WORD_TO_INT[word])
            policy.accepts_post_progression = True
            policy.evidence.append(_make_evidence(word_min))
            policy.confidence = "high"

    return policy


# ---------------------------------------------------------------------------
# 2. Disease stage policy
# ---------------------------------------------------------------------------
def extract_disease_stage_policy(elig_text: str, title: str = "") -> DiseaseStagePolicy:
    policy = DiseaseStagePolicy()
    if not elig_text:
        policy.confidence = "low"
        return policy

    text = elig_text + " " + title  # include title for stage hints
    text_lower = text.lower()

    # Hard signals — order matters
    is_metastatic = bool(re.search(r"\bmetastatic\b|\bstage iv\b|\bdistant metastas[ei]s\b|m1\b", text_lower))
    is_locally_advanced = bool(re.search(r"\blocally advanced\b|\blocal[- ]advanced\b|\bunresectable\b", text_lower))
    is_borderline = bool(re.search(r"\bborderline resectable\b|\bBRPC\b", text, re.IGNORECASE))
    is_resectable = bool(re.search(r"\bresectable\b(?!\s+or\s+borderline)|\bsurgically resectable\b", text_lower))
    is_recurrent = bool(re.search(r"\brecurrent\b", text_lower))
    is_neoadjuvant = bool(re.search(r"\bneoadjuvant\b|\bpreoperative\b", text_lower))
    is_adjuvant = bool(re.search(r"\badjuvant\b(?!\s*chemotherapy\s+for)", text_lower)) or bool(
        re.search(r"\bpostoperative\b|\bpost[- ]?surgical\b|\bafter (?:surgical )?resection\b", text_lower))
    is_conversion = bool(re.search(r"\bconversion (?:therapy|treatment)\b", text_lower))

    # Title tier (strong signal)
    title_lower = title.lower()
    title_neoadj_or_adj = bool(re.search(r"\bneoadjuvant\b|\badjuvant\b|\bresected\b|\bafter (?:surgery|resection)\b|\bborderline resectable\b|\bBRPC\b|\b(?:pre|post)operative\b|\bconversion (?:therapy|treatment)\b", title, re.IGNORECASE))
    title_metastatic = bool(re.search(r"\bmetastatic\b|\bstage iv\b|\brecurrent\b|\bunresectable\b", title_lower))

    # Setting determination
    if title_neoadj_or_adj or (is_neoadjuvant and not is_metastatic) or (is_adjuvant and not is_metastatic) or (is_conversion and not is_metastatic):
        if is_neoadjuvant or "neoadjuvant" in title_lower:
            policy.setting = "neoadjuvant"
        elif is_adjuvant or "adjuvant" in title_lower or "resected" in title_lower:
            policy.setting = "adjuvant"
        elif is_conversion:
            policy.setting = "conversion"
        else:
            policy.setting = "neoadjuvant"  # default for borderline-resectable
        policy.accepts_resectable = True
        policy.accepts_borderline_resectable = is_borderline or "borderline" in title_lower
        policy.accepts_metastatic = False
        policy.requires_metastatic = False
        policy.confidence = "high"
    elif title_metastatic or is_metastatic or is_recurrent:
        policy.setting = "metastatic"
        policy.accepts_metastatic = True
        policy.accepts_locally_advanced = is_locally_advanced
        policy.accepts_recurrent = is_recurrent
        # If title says "metastatic" only, require_metastatic = True
        if re.search(r"\bmetastatic\b", title_lower) and not is_locally_advanced:
            policy.requires_metastatic = True
        policy.confidence = "high"
    elif is_locally_advanced and not is_metastatic:
        policy.setting = "metastatic"  # locally advanced is closer to metastatic than to resectable
        policy.accepts_locally_advanced = True
        policy.accepts_metastatic = False
        policy.confidence = "medium"
    else:
        policy.setting = "mixed"
        policy.confidence = "low"

    # Evidence
    for label, hit in [("metastatic", is_metastatic), ("locally_advanced", is_locally_advanced),
                       ("borderline_resectable", is_borderline), ("resectable", is_resectable),
                       ("recurrent", is_recurrent), ("neoadjuvant", is_neoadjuvant),
                       ("adjuvant", is_adjuvant), ("conversion", is_conversion)]:
        if hit:
            m = re.search(label.replace("_", " ").replace("borderline resectable", r"borderline resectable|BRPC"),
                          text, re.IGNORECASE)
            if m:
                policy.evidence.append(Evidence(quote=text[max(0, m.start() - 40):m.end() + 40].strip(),
                                               field=f"stage:{label}", char_offset=m.start()))

    return policy


# ---------------------------------------------------------------------------
# 3. Mutation requirements
# ---------------------------------------------------------------------------
KRAS_VARIANTS = ["G12D", "G12C", "G12V", "G12R", "G12A", "G12S", "G13D", "G13C", "Q61H", "Q61L", "Q61R"]
def extract_mutation_requirements(elig_text: str, title: str = "") -> MutationRequirements:
    req = MutationRequirements()
    text = elig_text + " " + title
    text_lower = text.lower()

    # KRAS specific variants (find ALL — supports cohort-level "G12V or G12D" patterns)
    for variant in KRAS_VARIANTS:
        # Match: "KRAS G12D" / "KRAS-G12D" / "KRAS p.G12D" / "G12D mutation" (when in KRAS context)
        # Also: "G12V or G12D", "G12C/G12D" cohort-pair patterns
        patterns = [
            rf"KRAS[- ]?{variant}\b",
            rf"p\.?{variant}\b",
            rf"\b{variant}\b(?=[^.]*KRAS|[^.]*mutation)",  # variant within KRAS sentence
        ]
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                key = f"KRAS {variant}"
                if key not in req.required:
                    req.required.append(key)
                break

    # Pan-RAS / RAS-mutant inclusive
    if re.search(r"\bRAS[- ]mutant\b|\bRAS[- ]mutated\b|\bRAS[- ]mutation\b|\b(K|N|H)RAS mutation\b", text, re.IGNORECASE):
        if not any("KRAS" in r for r in req.required):
            req.required.append("RAS mutation (any)")
            req.pan_mutation_acceptable = True

    # KRAS wild-type exclusion
    if re.search(r"\bKRAS wild[- ]?type\b|\bRAS wild[- ]?type\b", text, re.IGNORECASE):
        req.excluded.append("KRAS/RAS wild-type")

    # Other key mutations seen in PDAC / NSCLC / CRC trials
    # IMPORTANT: only mark as required when target appears in MUTATION context
    # (not as a drug name target). E.g., "EGFR mutation" yes, "anti-EGFR antibody" no.
    other_targets_with_context = [
        ("EGFR", r"\bEGFR\s+(?:mutation|positive|sensitizing|exon\s*\d+|L858R|T790M|G719|amplification|alteration)\b"),
        ("BRAF V600E", r"\bBRAF\s*V600E\b"),
        ("HER2", r"\bHER2\s+(?:positive|amplification|overexpression|ihc|3\+|mutation|alteration)\b"),
        ("BRCA1", r"\bBRCA1\s+(?:mutation|deficient|alteration|germline)\b"),
        ("BRCA2", r"\bBRCA2\s+(?:mutation|deficient|alteration|germline)\b"),
        ("MTAP loss", r"\bMTAP\s+(?:loss|deletion|deficient|null)\b|\bMTAP[- ]?null\b"),
        ("MSI-H", r"\bMSI[- ]?H\b|microsatellite[- ]instability[- ]high"),
        ("dMMR", r"\bdMMR\b|mismatch[- ]repair[- ]deficient"),
        ("PALB2", r"\bPALB2\s+(?:mutation|alteration|germline)\b"),
        ("ATM", r"\bATM\s+(?:mutation|alteration|germline|deficient)\b"),
    ]
    for label, pat in other_targets_with_context:
        if re.search(pat, text, re.IGNORECASE):
            if label not in req.required:
                req.required.append(label)

    # Evidence
    for r in req.required:
        m = re.search(re.escape(r.split()[0]), text, re.IGNORECASE)
        if m:
            req.evidence.append(Evidence(quote=text[max(0, m.start() - 40):m.end() + 40].strip(),
                                         field="eligibilityCriteria",
                                         char_offset=m.start()))

    return req


# ---------------------------------------------------------------------------
# 4. Prior therapy exclusions
# ---------------------------------------------------------------------------
PRIOR_THERAPY_PATTERNS = [
    (r"prior treatment with (?:a |an |any )?(?:KRAS\s+G12D|KRAS\s+G12C|KRAS|pan[- ]?RAS|multi[- ]?RAS|RAS[- ]?ON)\s*(?:inhibitor|degrader|targeted therapy|inhibitor/degrader)?",
     "prior_RAS_targeted_therapy"),
    (r"prior treatment with (?:a |an )?KRAS G12D (?:inhibitor|degrader)",
     "prior_KRAS_G12D_inhibitor"),
    (r"(?:previous|prior) (?:targeted therapy against|treatment with) EGFR",
     "prior_EGFR_targeted_therapy"),
    (r"prior (?:treatment with )?(?:a |an )?(?:PRMT5|MAT2A) inhibitor",
     "prior_PRMT5_or_MAT2A_inhibitor"),
    (r"prior treatment with (?:a |an )?immune checkpoint inhibitor",
     "prior_checkpoint_inhibitor"),
    (r"previous KRAS inhibitors",
     "prior_KRAS_inhibitor"),
    (r"investigational KRAS G12D inhibitor",
     "prior_investigational_KRAS_G12D"),
    (r"received .{0,50}KRAS",
     "prior_KRAS_targeted"),
]


def extract_prior_therapy_exclusions(elig_text: str) -> list[str]:
    exclusions = []
    if not elig_text:
        return exclusions

    text_lower = elig_text.lower()
    # Look in exclusion section only (rough heuristic)
    excl_idx = text_lower.find("exclusion")
    excl_text = elig_text[excl_idx:] if excl_idx >= 0 else elig_text

    for pat, label in PRIOR_THERAPY_PATTERNS:
        if re.search(pat, excl_text, re.IGNORECASE):
            if label not in exclusions:
                exclusions.append(label)
    return exclusions


# ---------------------------------------------------------------------------
# 5. HLA + biomarker requirements
# ---------------------------------------------------------------------------
def extract_hla_requirements(elig_text: str) -> list[str]:
    """
    Extract required HLA alleles. Handles:
      - Full subtype: HLA-A*11:01
      - Allele only: HLA-A*11 / HLA-C*08
      - Comma/conjunction lists: "HLA-A*11, C*01:02, or C*08:02"
      - Escaped asterisk: HLA-A\\*11:01
    """
    if not elig_text:
        return []

    hla_alleles = set()

    # Step 1: full HLA-X*NN:NN
    for m in re.finditer(r"HLA[- ]?([ABC]|DRB1|DPB1|DQB1)\s*\\?\*\s*(\d{1,3}):(\d{1,3})", elig_text):
        locus, group, subtype = m.group(1), m.group(2), m.group(3)
        hla_alleles.add(f"HLA-{locus}*{group}:{subtype}")

    # Step 2: allele-only HLA-X*NN (no subtype). Use negative lookahead to refuse continuation digit/":\d"
    for m in re.finditer(r"HLA[- ]?([ABC]|DRB1|DPB1|DQB1)\s*\\?\*\s*(\d{1,3})(?![\d:])", elig_text):
        locus, group = m.group(1), m.group(2)
        token = f"HLA-{locus}*{group}"
        # don't add if a more specific subtype already captured
        if not any(a.startswith(token + ":") for a in hla_alleles):
            hla_alleles.add(token)

    # Step 3: comma/conjunction patterns — "HLA-A*11, C*01:02, or C*08:02"
    # When previous match scoped only to first locus, scan continuation context
    list_match = re.search(
        r"HLA[- ]?[ABC]\s*\\?\*\s*\d{1,3}(?::\d{1,3})?\s*[,，]\s*[ABC]\s*\\?\*\s*\d{1,3}(?::\d{1,3})?",
        elig_text,
    )
    if list_match:
        # parse the entire allele list
        list_text = elig_text[list_match.start():list_match.end() + 100]
        for m2 in re.finditer(r"\b([ABC])\s*\\?\*\s*(\d{1,3})(?::(\d{1,3}))?(?![\d])", list_text):
            locus, group, subtype = m2.group(1), m2.group(2), m2.group(3)
            if subtype:
                hla_alleles.add(f"HLA-{locus}*{group}:{subtype}")
            else:
                token = f"HLA-{locus}*{group}"
                if not any(a.startswith(token + ":") for a in hla_alleles):
                    hla_alleles.add(token)

    return sorted(hla_alleles)


BIOMARKER_PATTERNS = [
    (r"CLDN\s*18\.2|claudin\s*18\.2", "CLDN18.2"),
    (r"\bMSLN\b|mesothelin", "MSLN"),
    (r"\bMTAP\b", "MTAP"),
    (r"HER2\s*(?:positive|amplification|over[- ]?expression|ihc|3\+)", "HER2+"),
    (r"PD[- ]?L1\b", "PD-L1"),
    (r"\bTROP[- ]?2\b", "TROP2"),
    (r"\bCEA[- ]?(?:positive|expression)\b", "CEA+"),
    (r"\bGUCY2C\b", "GUCY2C"),
    (r"\bDLL3\b", "DLL3"),
    (r"\bCD\d+\b", "CD-marker"),
]


def extract_biomarker_requirements(elig_text: str) -> list[str]:
    if not elig_text:
        return []
    found = []
    text_lower = elig_text.lower()
    incl_idx = text_lower.find("inclusion")
    incl_text = elig_text[incl_idx:incl_idx + 4000] if incl_idx >= 0 else elig_text[:4000]

    for pat, label in BIOMARKER_PATTERNS:
        if re.search(pat, incl_text, re.IGNORECASE):
            if label not in found:
                found.append(label)
    return found


# ---------------------------------------------------------------------------
# Top-level extractor
# ---------------------------------------------------------------------------
def extract_metadata(trial: dict) -> TrialMetadata:
    """Run all extractors against a single trial dict."""
    elig_text = trial.get("eligibility_full") or trial.get("eligibility_excerpt", "")
    title = trial.get("title", "")
    interventions = trial.get("interventions", [])

    metadata = TrialMetadata(
        treatment_line_policy=extract_treatment_line_policy(elig_text, title),
        disease_stage_policy=extract_disease_stage_policy(elig_text, title),
        mutation_requirements=extract_mutation_requirements(elig_text, title),
        prior_therapy_exclusions=extract_prior_therapy_exclusions(elig_text),
        hla_requirements=extract_hla_requirements(elig_text),
        biomarker_requirements=extract_biomarker_requirements(elig_text),
        trial_type=detect_trial_type(title, interventions, elig_text),
    )
    return metadata


def metadata_to_dict(md: TrialMetadata) -> dict:
    """Convert dataclass to JSON-friendly dict."""
    d = asdict(md)
    # convert Evidence dataclasses (already dict-like via asdict)
    return d


def annotate_trials(trials: list[dict]) -> list[dict]:
    """Add 'metadata' field to each trial."""
    for t in trials:
        if "error" in t:
            continue
        md = extract_metadata(t)
        t["metadata"] = metadata_to_dict(md)
    return trials


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Extract structured trial metadata (v1.6.0)")
    parser.add_argument("--in", dest="input", required=True, help="Input JSON (e.g., nct_results.json)")
    parser.add_argument("--out", required=True, help="Output JSON with metadata field added")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "included_trials" in data:
        data["included_trials"] = annotate_trials(data["included_trials"])
        if "excluded_trials" in data:
            data["excluded_trials"] = annotate_trials(data["excluded_trials"])
        if "all_trials" in data:
            data["all_trials"] = annotate_trials(data["all_trials"])
    elif isinstance(data, list):
        data = annotate_trials(data)
    else:
        print("Unsupported input shape", file=sys.stderr)
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    n_trials = len(data["included_trials"]) if isinstance(data, dict) else len(data)
    print(f"Annotated {n_trials} trials. Output: {args.out}")


if __name__ == "__main__":
    main()
