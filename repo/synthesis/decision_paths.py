"""
decision_paths.py — v1.6.0 P0.1

Decision Report Top N synthesizer.

Produces 1-3 actionable decision paths from the gated + scored + verified +
risk + efficacy-annotated trials.

Path selection rules:
  1. Sort match-bucket trials by composite_score = feasibility × efficacy_signal
  2. Apply diversity constraint:
     - At most 2 paths sharing same drug-class mechanism
     - At least 1 path immediately actionable in patient's home country (when available)
  3. Top N = min(3, available_paths). Soft constraint — explanation if <3.
  4. Each path includes vs SoC head-to-head + risk profile + timeline + blockers status

Output schema (per path):
  rank, path_type, trial, rationale, vs_soc, feasibility, risks, timeline, blockers
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from data import ontology_loader as ont  # noqa: E402


@dataclass
class DecisionPath:
    rank: int
    path_type: str  # primary / secondary / bridging / fallback / overseas
    trial_id: str
    trial_title: str
    sponsor: str
    phase: str
    sites_in_country: int
    rationale_one_liner: str
    rationale_detailed: str
    vs_soc: dict
    feasibility: dict
    risks: list[dict]
    timeline: dict
    blockers_status: dict
    efficacy_snapshot: Optional[dict] = None
    # v1.7 additions
    alternatives_comparison: list[dict] = field(default_factory=list)  # ["why this over X/Y"]
    consequences_of_skipping: str = ""
    v17_flags: dict = field(default_factory=dict)  # phase3_risk, chemo_overlap, demotion


def _drug_class_of(trial: dict) -> str:
    """Identify primary mechanism class for diversity constraint (narrow)."""
    risks = [r.get("key") for r in trial.get("risk_profiles", [])]

    if "kras_g12d_first_in_class" in risks:
        return "kras_g12d_inhibitor"
    if "pan_ras_inhibitor" in risks:
        return "pan_ras_inhibitor"
    if "tcr_t_pdac" in risks:
        return "tcr_t"
    if "car_t_solid_tumor" in risks:
        return "car_t"
    if "til_solid_tumor" in risks:
        return "til"
    if "claudin18_2_targeted" in risks:
        return "cldn18_2"
    if "msln_targeted" in risks:
        return "msln"
    if "kras_neoantigen_vaccine" in risks:
        return "kras_vaccine"
    if "egfr_combo_pdac" in risks:
        return "egfr_combo"
    return "other"


def _mechanism_family_of(trial: dict) -> str:
    """Broader family classification for diversity (small_molecule / cell_therapy / adc / combo / other)."""
    cls = _drug_class_of(trial)
    if cls in ("kras_g12d_inhibitor", "pan_ras_inhibitor"):
        return "small_molecule_targeted"
    if cls in ("tcr_t", "car_t", "til"):
        return "cell_therapy"
    if cls in ("cldn18_2", "msln"):
        return "adc_or_targeted_biologic"
    if cls in ("kras_vaccine",):
        return "vaccine"
    if cls in ("egfr_combo",):
        return "small_molecule_targeted"  # combos
    return "other"


def _composite_relevance(trial: dict) -> float:
    """Combined relevance score: feasibility × efficacy_signal."""
    fs = trial.get("feasibility", {})
    base = fs.get("composite", 0.5)

    # Boost for trials with strong NCT-level efficacy data
    eff = trial.get("efficacy") or {}
    if eff.get("match_type") == "exact_nct":
        boost = 0.20
    elif eff.get("match_type") == "drug_match":
        boost = 0.10
    elif eff.get("match_type") == "drug_class_baseline":
        boost = 0.0
    else:
        boost = -0.05  # no efficacy data

    # Confidence penalty from gating (if any blockers pending)
    penalty = trial.get("gating", {}).get("confidence_penalty", 0)
    return round(base + boost - penalty, 3)


def _to_float(x):
    """Normalize ORR/PFS values which may be float, '0.41', '10-15%', '5-7', None."""
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip().rstrip("%").replace("月", "").strip()
        try:
            return float(s)
        except ValueError:
            # range "10-15" or "5-7" → take midpoint
            for sep in ("-", "–", "—", "~"):
                if sep in s:
                    parts = s.split(sep)
                    try:
                        a, b = float(parts[0].rstrip("%")), float(parts[1].rstrip("%"))
                        v = (a + b) / 2
                        # if percent string, scale to 0-1
                        return v / 100.0 if v > 1 else v
                    except (ValueError, IndexError):
                        pass
            return None
    return None


def _orr_to_unit(x):
    """Normalize ORR to 0-1 unit (handles '0.30' OR '30%' OR '10-15%')."""
    v = _to_float(x)
    if v is None:
        return None
    return v / 100.0 if v > 1 else v


def evaluate_demotion(trial: dict, soc_regimens: list[dict]) -> dict:
    """
    v1.7 P0.1 — Demotion rule:
    If trial's predicted ORR < SoC ORR AND predicted PFS < SoC PFS
    AND data source is class_estimate (not NCT-level),
    demote out of Decision Report.

    Cell therapy (TCR-T / CAR-T / TIL) gets a DoR exemption: not auto-demoted
    on ORR alone, because responders typically have longer durable response.

    Returns: {demote: bool, reason: str, comparison: dict}
    """
    eff = trial.get("efficacy") or {}
    if not eff or not soc_regimens:
        return {"demote": False, "reason": "Insufficient data to compare with SoC"}

    soc = soc_regimens[0]
    metrics = eff.get("metrics", {})

    trial_orr = _orr_to_unit(metrics.get("orr") or metrics.get("orr_pdac")
                              or metrics.get("expected_orr_2L_pdac") or metrics.get("orr_estimate"))
    trial_pfs = _to_float(metrics.get("median_pfs_months") or metrics.get("median_pfs_months_pdac")
                          or metrics.get("expected_pfs_months"))
    soc_orr = _orr_to_unit(soc.get("orr") or soc.get("orr_estimate"))
    soc_pfs = _to_float(soc.get("median_pfs_months") or soc.get("median_pfs_months_estimate"))

    risks = [r.get("key") for r in trial.get("risk_profiles", [])]
    is_cell_therapy = any(r in risks for r in ("tcr_t_pdac", "car_t_solid_tumor", "til_solid_tumor"))
    is_class_estimate = (eff.get("match_type") == "drug_class_baseline")

    # Cell therapy exemption: only demote on PFS, not ORR (responders have long DoR)
    if is_cell_therapy:
        if trial_pfs is not None and soc_pfs is not None and trial_pfs < soc_pfs * 0.7:
            return {
                "demote": True,
                "reason": f"Cell therapy 预期 mPFS ({trial_pfs}) < SoC ({soc_pfs}) × 0.7 — DoR 优势不足以补偿；class_estimate={is_class_estimate}",
                "comparison": {"trial_pfs": trial_pfs, "soc_pfs": soc_pfs},
            }
        return {"demote": False, "reason": "Cell therapy with DoR-exemption pass"}

    # Strict rule for class_estimate: demote only if BOTH ORR and PFS clearly worse
    if is_class_estimate:
        worse_orr = trial_orr is not None and soc_orr is not None and trial_orr < soc_orr * 0.7
        worse_pfs = trial_pfs is not None and soc_pfs is not None and trial_pfs < soc_pfs * 0.8
        if worse_orr and worse_pfs:
            return {
                "demote": True,
                "reason": f"机制类基线显著劣于 SoC（ORR {trial_orr:.2f}<{soc_orr*0.7:.2f}；mPFS {trial_pfs}<{soc_pfs*0.8}）",
                "comparison": {"trial_orr": trial_orr, "soc_orr": soc_orr, "trial_pfs": trial_pfs, "soc_pfs": soc_pfs},
            }

    return {"demote": False, "reason": "Trial competitive with SoC or NCT-level data available"}


def evaluate_chemo_backbone_overlap(trial: dict, patient: dict) -> dict:
    """
    v1.7 P0.3 — Chemo backbone overlap penalty.
    Detect overlap between trial chemo backbone and patient's prior failed therapy.
    Generalized: chemo regimen list comes from data/clinical_ontology.json.

    Returns: {penalty: float (0-1), reason: str}
    """
    interventions = " ".join(trial.get("interventions", [])).lower()
    title = trial.get("title", "").lower()
    trial_text = interventions + " " + title
    patient_prior_text = " ".join(patient.get("prior_therapies", [])).lower()

    overlaps = ont.find_chemo_overlap(trial_text, patient_prior_text)
    if overlaps:
        regimen_names = [o["regimen"] for o in overlaps]
        return {
            "penalty": 0.25,
            "reason": f"试验骨架包含患者已失败的化疗方案: {regimen_names} — 交叉耐药风险",
            "overlapping_groups": regimen_names,
            "details": overlaps,
        }
    return {"penalty": 0.0, "reason": "No chemo backbone overlap with patient's prior failures"}


def evaluate_targeted_therapy_class_overlap(trial: dict, patient: dict) -> dict:
    """
    v1.7.1 — Generalize beyond chemo: also detect targeted-therapy class overlap.
    Example: NSCLC patient on osimertinib who progressed → trial of another EGFR-TKI
    (lazertinib, almonertinib) is class-overlap. Trial of MET inhibitor (savolitinib)
    is NOT — different class.
    """
    interventions = " ".join(trial.get("interventions", [])).lower()
    title = trial.get("title", "").lower()
    trial_text = interventions + " " + title
    pat_priors = patient.get("prior_therapies", [])

    # Patient's already-received classes
    patient_classes = ont.detect_therapy_classes_in_history(pat_priors)
    # Trial's intervention classes
    trial_classes = []
    for class_name, info in ont.get_all_therapy_classes().items():
        if any(c.lower() in trial_text for c in info.get("components", []) + info.get("aliases", [])):
            trial_classes.append(class_name)

    overlap = list(set(patient_classes) & set(trial_classes))
    # Filter out cases where trial is the SAME drug class but is testing a NEXT-GEN drug
    # (e.g., 4th-gen EGFR-TKI after 3rd-gen failure) — those are clinically valid.
    # Heuristic: if trial title contains "after progression" / "post" / "resistance", do NOT penalize.
    if any(k in trial_text for k in ["after progression", "post-progression", "resistance", "pretreated",
                                       "previously treated", "post-tki", "post-kras"]):
        return {
            "penalty": 0.0,
            "reason": f"Same-class therapy ({overlap}) but trial is post-progression / next-generation — likely intentional",
            "overlap": overlap,
            "exempted": True,
        }

    if overlap:
        return {
            "penalty": 0.15,  # lighter than chemo backbone (0.25)
            "reason": f"Targeted-therapy class overlap: {overlap} — patient already received this class, retreatment less likely to benefit",
            "overlap": overlap,
        }
    return {"penalty": 0.0, "reason": "No targeted-therapy class overlap"}


def _safe_metric(v, fmt: str = "raw") -> str:
    """Render a metric value safely. fmt='percent' converts 0.41 → '41%'."""
    if v is None:
        return "N/A"
    if isinstance(v, str):
        return v
    if fmt == "percent":
        if isinstance(v, (int, float)):
            return f"{int(v*100)}%" if v <= 1 else f"{v}%"
    return str(v)


def detect_phase_3_randomization_risk(trial: dict) -> dict:
    """v1.7 — Flag Phase 3 RCTs where patient may be assigned to control arm."""
    phases = trial.get("phases", []) + trial.get("verification", {}).get("phases_official", [])
    if "PHASE3" in phases:
        return {
            "flag": True,
            "note": "Phase 3 RCT — 可能被随机分配到对照组（标准化疗或安慰剂），无法保证拿到试验药",
        }
    return {"flag": False}


def _vs_soc_compare(trial: dict, soc_regimens: list[dict]) -> dict:
    """Build head-to-head comparison structure."""
    eff = trial.get("efficacy") or {}
    metrics = eff.get("metrics", {})

    # Pick most relevant SoC (first in list = highest priority for that line)
    if not soc_regimens:
        return {"available": False, "note": "SoC benchmarks not available for this cancer/line."}

    soc = soc_regimens[0]
    trial_orr = metrics.get("orr") or metrics.get("orr_pdac") or metrics.get("expected_orr_2L_pdac") or metrics.get("orr_estimate")
    trial_pfs = metrics.get("median_pfs_months") or metrics.get("median_pfs_months_pdac") or metrics.get("expected_pfs_months")
    soc_orr = soc.get("orr") or soc.get("orr_estimate")
    soc_pfs = soc.get("median_pfs_months") or soc.get("median_pfs_months_estimate")

    deltas = {}
    try:
        if isinstance(trial_orr, (int, float)) and isinstance(soc_orr, (int, float)):
            deltas["orr_delta"] = round(trial_orr - soc_orr, 2)
    except (TypeError, ValueError):
        pass

    return {
        "available": True,
        "trial_id": trial.get("id"),
        "trial_orr": trial_orr,
        "trial_median_pfs": trial_pfs,
        "trial_data_source": eff.get("source", {}).get("citation", "data unavailable"),
        "trial_maturity": eff.get("maturity", "unknown"),
        "soc_regimen": soc.get("regimen", "?"),
        "soc_median_os": soc.get("median_os_months") or soc.get("median_os_months_estimate"),
        "soc_orr": soc_orr,
        "soc_median_pfs": soc_pfs,
        "soc_pivotal": soc.get("pivotal", "?"),
        "soc_caveats": soc.get("caveats"),
        "deltas": deltas,
    }


def _timeline_estimate(trial: dict, patient: dict) -> dict:
    """Estimate screening + first-dose timeline."""
    today = dt.date.today()
    risks = [r.get("key") for r in trial.get("risk_profiles", [])]

    # Base screening window
    screening_days = 14

    if "tcr_t_pdac" in risks or "car_t_solid_tumor" in risks or "til_solid_tumor" in risks:
        manufacture_days = 35  # 4-5 weeks
        first_dose_offset = screening_days + manufacture_days
        critical_path = "HLA typing (if TCR-T) → leukapheresis → cell manufacture (4-8 wk) → lymphodepletion → infusion"
    elif "overseas_us_eu" in risks:
        screening_days = 30  # add visa + travel
        first_dose_offset = 45
        critical_path = "B1/B2 visa application (4-6 wk) → screening visit → first dose"
    else:
        first_dose_offset = 21
        critical_path = "FOLFIRINOX last-dose verification → CNS MRI → screening visit → C1D1"

    screening_window_start = today
    screening_window_end = today + dt.timedelta(days=screening_days)
    first_dose_estimate = today + dt.timedelta(days=first_dose_offset)

    return {
        "screening_window": f"{screening_window_start.isoformat()} to {screening_window_end.isoformat()}",
        "expected_first_dose": first_dose_estimate.isoformat(),
        "critical_path": critical_path,
        "manufacture_or_visa_days": first_dose_offset - screening_days if first_dose_offset > screening_days else 0,
    }


def _path_type_for(trial: dict, rank: int) -> str:
    risks = [r.get("key") for r in trial.get("risk_profiles", [])]
    overseas = "overseas_us_eu" in risks
    if rank == 1:
        return "primary_overseas" if overseas else "primary"
    if "tcr_t_pdac" in risks or "car_t_solid_tumor" in risks or "til_solid_tumor" in risks:
        return "secondary_cell_therapy"
    if overseas:
        return "secondary_overseas"
    return "secondary"


def _has_cancer_mismatch(trial: dict) -> bool:
    """Return True if verifier flagged a cancer-condition mismatch."""
    mm = trial.get("verification", {}).get("mismatches", []) or []
    return any("do not include patient cancer" in m for m in mm)


def synthesize(gated_data: dict, patient: dict, top_n: int = 3) -> dict:
    """Main entry. Returns Decision Report dict."""
    soc_regimens = gated_data.get("soc_benchmarks", [])
    # Filter out (a) trials demoted by feasibility, (b) trials with cancer-type mismatch
    # at the verifier level (e.g., NSCLC trial slipped into PDAC retrieval)
    matched = [
        t for t in gated_data.get("match", [])
        if t.get("feasibility", {}).get("promote_to_decision_report", True)
        and not _has_cancer_mismatch(t)
    ]
    # Also drop trials whose verification status is not RECRUITING (covers ACTIVE_NOT_RECRUITING etc.)
    matched = [t for t in matched if t.get("verification", {}).get("overall_status", "RECRUITING") == "RECRUITING"]

    # v1.7 P0.1 — Demotion rule: predicted < SoC → demote
    # v1.7 P0.3 — Chemo backbone overlap penalty
    # v1.7.1 — Targeted-therapy class overlap (generalizes chemo to all targeted classes)
    # NB: also annotate ALL trials (not just promote candidates) so Match List can show flags too
    demoted_reasons = {}
    refined_matched = []
    chemo_overlap_count = 0
    targeted_overlap_count = 0
    phase3_count = 0
    demoted_count = 0
    for t in matched:
        demotion = evaluate_demotion(t, soc_regimens)
        chemo_overlap = evaluate_chemo_backbone_overlap(t, patient)
        targeted_overlap = evaluate_targeted_therapy_class_overlap(t, patient)
        phase3_risk = detect_phase_3_randomization_risk(t)

        # Persist on trial dict so it reaches downstream consumers
        t["_v17_flags"] = {
            "demotion": demotion,
            "chemo_overlap": chemo_overlap,
            "targeted_overlap": targeted_overlap,
            "phase3_risk": phase3_risk,
        }
        # Combined penalty: chemo + targeted (capped to avoid double-discount)
        total_overlap_penalty = min(chemo_overlap["penalty"] + targeted_overlap["penalty"], 0.35)
        if total_overlap_penalty > 0:
            t["_chemo_overlap_penalty"] = total_overlap_penalty
        if chemo_overlap["penalty"] > 0:
            chemo_overlap_count += 1
        if targeted_overlap["penalty"] > 0:
            targeted_overlap_count += 1
        if phase3_risk["flag"]:
            phase3_count += 1

        if demotion["demote"]:
            demoted_count += 1
            demoted_reasons[t["id"]] = demotion["reason"]
            t["gating"]["v17_demoted"] = True
            t["gating"]["v17_demotion_reason"] = demotion["reason"]
            continue
        refined_matched.append(t)

    matched = refined_matched
    # also annotate trials in conditional + exclude buckets so the Match List can show flags
    for bucket_name in ("conditional", "exclude"):
        for t in gated_data.get(bucket_name, []):
            if "_v17_flags" not in t:
                t["_v17_flags"] = {
                    "demotion": evaluate_demotion(t, soc_regimens),
                    "chemo_overlap": evaluate_chemo_backbone_overlap(t, patient),
                    "targeted_overlap": evaluate_targeted_therapy_class_overlap(t, patient),
                    "phase3_risk": detect_phase_3_randomization_risk(t),
                }

    # Add composite relevance (subtract chemo overlap penalty)
    for t in matched:
        rel = _composite_relevance(t)
        rel -= t.get("_chemo_overlap_penalty", 0.0)
        t["_relevance"] = round(rel, 3)

    # Sort by relevance descending
    sorted_trials = sorted(matched, key=lambda x: -x["_relevance"])

    # Bucketed selection — produces multi-axis diversity (geography × mechanism × sponsor)
    # Buckets:
    #   B1: domestic_best      → highest-relevance trial in patient's home country
    #   B2: overseas_best      → highest-relevance trial outside (if patient willing_to_travel)
    #   B3: alt_mechanism      → best trial in a different mechanism family from B1
    #   B4: domestic_secondary → next best domestic if B2/B3 not applicable
    pat_country = patient.get("country", "China")
    willing_travel = patient.get("willing_to_travel_internationally", False)

    def primary_drug(trial):
        ivs = trial.get("interventions", [])
        return ivs[0].lower() if ivs else trial.get("title", "")[:30].lower()

    def is_domestic(t):
        return (t.get("china_site_count", 0) > 0 and pat_country == "China")

    def overseas(t):
        return not is_domestic(t)

    domestic_sorted = [t for t in sorted_trials if is_domestic(t)]
    overseas_sorted = [t for t in sorted_trials if overseas(t)]

    # Bucket 1: best domestic
    paths = []
    used_ids = set()
    used_families = {}
    used_sponsors = {}
    used_drugs = {}

    def can_pick(t, allow_same_family=False):
        family = _mechanism_family_of(t)
        sponsor = t.get("sponsor", "?")
        drug = primary_drug(t)
        if not allow_same_family:
            if used_families.get(family, 0) >= 2 and family != "other":
                return False
        if used_sponsors.get(sponsor, 0) >= 2:
            return False
        if used_drugs.get(drug, 0) >= 2:
            return False
        return True

    def commit(t):
        family = _mechanism_family_of(t)
        sponsor = t.get("sponsor", "?")
        drug = primary_drug(t)
        used_ids.add(t["id"])
        used_families[family] = used_families.get(family, 0) + 1
        used_sponsors[sponsor] = used_sponsors.get(sponsor, 0) + 1
        used_drugs[drug] = used_drugs.get(drug, 0) + 1
        paths.append(t)

    # B1: best domestic
    for t in domestic_sorted:
        if t["id"] not in used_ids and can_pick(t):
            commit(t)
            break

    # B2: best overseas (if patient willing & top_n > 1)
    if len(paths) < top_n and willing_travel:
        for t in overseas_sorted:
            if t["id"] not in used_ids and can_pick(t):
                commit(t)
                break

    # B3: best alternative mechanism (different family than already picked)
    # Prefer KNOWN families (cell_therapy, adc_or_biologic, vaccine) over "other"
    if len(paths) < top_n:
        existing_families = set(_mechanism_family_of(p) for p in paths)
        known_alt_families = {"cell_therapy", "adc_or_targeted_biologic", "vaccine"} - existing_families

        # Pass 1: prefer trials in a known alternative family
        picked_alt = False
        for t in sorted_trials:
            if t["id"] in used_ids:
                continue
            family = _mechanism_family_of(t)
            if family in known_alt_families and can_pick(t):
                commit(t)
                picked_alt = True
                break

        # Pass 2: if no known alt family available, fall back to any non-existing family
        if not picked_alt:
            for t in sorted_trials:
                if t["id"] in used_ids:
                    continue
                if _mechanism_family_of(t) not in existing_families and can_pick(t):
                    commit(t)
                    break

    # B4: fill remaining slots with next best (relax family cap to 3)
    while len(paths) < top_n:
        picked_one = False
        for t in sorted_trials:
            if t["id"] in used_ids:
                continue
            family = _mechanism_family_of(t)
            sponsor = t.get("sponsor", "?")
            drug = primary_drug(t)
            if used_families.get(family, 0) >= 3 and family != "other":
                continue
            if used_sponsors.get(sponsor, 0) >= 2 or used_drugs.get(drug, 0) >= 2:
                continue
            commit(t)
            picked_one = True
            break
        if not picked_one:
            break

    has_domestic = any(is_domestic(p) for p in paths)

    # ----------------------------------------------------------------------
    # v1.7 P1.1 — Build alternatives comparison
    # For each picked path, identify the next-best 2 trials in same family
    # (NOT picked) and explain why this one was preferred
    # ----------------------------------------------------------------------
    def build_alternatives_comparison(picked: dict, all_sorted: list[dict]) -> list[dict]:
        comparisons = []
        picked_family = _mechanism_family_of(picked)
        picked_rel = picked.get("_relevance", 0)
        same_family_alternatives = [
            t for t in all_sorted
            if t["id"] != picked["id"]
            and _mechanism_family_of(t) == picked_family
            and t.get("id") not in [p["id"] for p in paths]
        ][:2]

        for alt in same_family_alternatives:
            alt_rel = alt.get("_relevance", 0)
            reasons = []

            # Compare feasibility composite
            picked_fs = picked.get("feasibility", {}).get("composite", 0)
            alt_fs = alt.get("feasibility", {}).get("composite", 0)
            if picked_fs > alt_fs + 0.05:
                reasons.append(f"feasibility {picked_fs} > {alt_fs}")

            # China sites
            p_cn = picked.get("china_site_count", 0)
            a_cn = alt.get("china_site_count", 0)
            if p_cn > a_cn:
                reasons.append(f"中国中心 {p_cn} > {a_cn}")

            # Efficacy data quality
            picked_eff = picked.get("efficacy", {}) or {}
            alt_eff = alt.get("efficacy", {}) or {}
            if picked_eff.get("match_type") == "exact_nct" and alt_eff.get("match_type") != "exact_nct":
                reasons.append("有 NCT-level 真实 ORR 数据 vs alternative 仅有机制类基线")

            # Chemo overlap
            p_chemo = picked.get("_chemo_overlap_penalty", 0)
            a_chemo = alt.get("_chemo_overlap_penalty", 0)
            if a_chemo > p_chemo:
                reasons.append("alternative 化疗骨架与患者既往失败方案重叠")

            # Phase 3 randomization
            picked_phases = picked.get("phases", [])
            alt_phases = alt.get("phases", [])
            if "PHASE3" in alt_phases and "PHASE3" not in picked_phases:
                reasons.append("alternative 是 Phase 3 RCT — 可能被随机到对照组")

            if not reasons:
                reasons.append(f"composite relevance {picked_rel} > {alt_rel}（边际差异）")

            comparisons.append({
                "alternative_id": alt["id"],
                "alternative_title": alt.get("title", "")[:80],
                "why_picked_won": reasons,
            })
        return comparisons

    def build_consequences_of_skipping(picked: dict, soc_list: list[dict], rank: int) -> str:
        """v1.7 P1.3 — Explain what's lost if patient skips this path."""
        eff = picked.get("efficacy", {}) or {}
        metrics = eff.get("metrics", {})
        orr = metrics.get("orr") or metrics.get("orr_pdac") or metrics.get("expected_orr_2L_pdac")
        pfs = metrics.get("median_pfs_months") or metrics.get("median_pfs_months_pdac") or metrics.get("expected_pfs_months")

        if not soc_list:
            return f"如不入组本试验，可考虑下一条决策路径或回到标准治疗。"

        soc = soc_list[0]
        soc_orr = soc.get("orr") or soc.get("orr_estimate")
        soc_pfs = soc.get("median_pfs_months") or soc.get("median_pfs_months_estimate")

        parts = []
        if rank == 1:
            parts.append(
                f"放弃本路径 = 失去 {eff.get('drug') or eff.get('drug_class', '该药/机制')} 的"
                f"预期 ORR ({_safe_metric(orr, 'percent')}) 与 mPFS ({_safe_metric(pfs)} 月)；"
            )
            parts.append(
                f"回到 SoC（{soc.get('regimen')}）的预期 ORR {_safe_metric(soc_orr, 'percent')} / "
                f"mPFS {_safe_metric(soc_pfs)} 月；"
            )
            try:
                if isinstance(orr, (int, float)) and isinstance(soc_orr, (int, float)):
                    delta = orr - soc_orr
                    if delta > 0:
                        parts.append(f"绝对 ORR 差距约 +{int(delta*100)}% — 直接放弃本路径的机会成本较高。")
            except (TypeError, ValueError):
                pass
        else:
            parts.append(
                f"本路径若不进，仍可走 Path #1 / SoC ({soc.get('regimen')})；"
                f"主要价值在于提供机制多样性（{eff.get('drug_class') or '替代机制'}）作为 #1 失败/不可入时的备选。"
            )

        return " ".join(parts)

    # Build path objects
    decision_paths = []
    for i, t in enumerate(paths):
        rank = i + 1
        eff = t.get("efficacy")
        vs_soc = _vs_soc_compare(t, soc_regimens)
        timeline = _timeline_estimate(t, patient)
        risks = t.get("risk_profiles", [])
        gating = t.get("gating", {})
        verification = t.get("verification", {})
        v17_flags = t.get("_v17_flags", {})
        alternatives = build_alternatives_comparison(t, sorted_trials)
        consequences = build_consequences_of_skipping(t, soc_regimens, rank)

        rationale_pieces = []
        if eff and eff.get("match_type") == "exact_nct":
            metrics = eff.get("metrics", {})
            orr = metrics.get("orr") or metrics.get("orr_pdac")
            pfs = metrics.get("median_pfs_months") or metrics.get("median_pfs_months_pdac")
            if orr:
                rationale_pieces.append(f"已发表 ORR {int(orr*100)}%")
            if pfs:
                rationale_pieces.append(f"mPFS {pfs} 月")
        elif eff and eff.get("match_type") == "drug_class_baseline":
            rationale_pieces.append(f"机制类基线（{eff.get('drug_class','?')}）")
        rationale_pieces.append(f"feasibility {t['feasibility']['composite']}")
        if t.get("china_site_count", 0):
            rationale_pieces.append(f"中国 {t['china_site_count']} 中心")
        else:
            rationale_pieces.append("海外（需出行）")

        # Detailed rationale
        detailed = []
        if gating.get("blockers_satisfied"):
            detailed.append(f"已满足的硬条件: {gating['blockers_satisfied']}")
        if gating.get("blockers_pending"):
            detailed.append(f"待补的硬条件: {gating['blockers_pending']}")
        detailed.append(f"试验状态: {verification.get('overall_status','?')}; 末次更新: {verification.get('last_update_date','?')}")

        dp = DecisionPath(
            rank=rank,
            path_type=_path_type_for(t, rank),
            trial_id=t.get("id"),
            trial_title=t.get("title"),
            sponsor=t.get("sponsor", "?"),
            phase="/".join(t.get("phases", [])),
            sites_in_country=t.get("china_site_count", 0),
            rationale_one_liner=" + ".join(rationale_pieces),
            rationale_detailed="\n".join(detailed),
            vs_soc=vs_soc,
            feasibility=t.get("feasibility", {}),
            risks=risks,
            timeline=timeline,
            blockers_status={
                "satisfied": gating.get("blockers_satisfied", []),
                "pending": gating.get("blockers_pending", []),
                "advisors_unknown": gating.get("advisors_unknown", []),
            },
            efficacy_snapshot=eff,
            alternatives_comparison=alternatives,
            consequences_of_skipping=consequences,
            v17_flags=v17_flags,
        )
        decision_paths.append(asdict(dp))

    # If no paths qualified, surface that fact
    diagnostic = ""
    if not decision_paths:
        diagnostic = "No trials passed the feasibility-promote threshold (any sub-score < 0.30 demoted). See Match List for full inventory."
    elif len(decision_paths) < top_n:
        diagnostic = f"Only {len(decision_paths)} path(s) met diversity + feasibility thresholds. Reasons: limited drug-class diversity in available trials and/or restrictive blockers."

    # Domestic-path warning
    if not has_domestic and pat_country == "China" and any(p.get("sites_in_country", 0) == 0 for p in decision_paths):
        diagnostic += "\n⚠️ No immediately-actionable domestic (China) path in Top N. All listed paths require international travel."

    return {
        "patient_summary": patient.get("summary", ""),
        "decision_paths": decision_paths,
        "diagnostic": diagnostic,
        "soc_benchmarks": soc_regimens,
        "match_inventory_size": len(matched),
        "v17_summary": {
            "chemo_overlap_count": chemo_overlap_count,
            "phase3_rct_count": phase3_count,
            "demoted_count": demoted_count,
            "demoted_reasons": demoted_reasons,
        },
        "report_version": "v1.7.0",
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
    }


# CLI
if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--patient", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    with open(args.patient) as f:
        patient = json.load(f)

    report = synthesize(data, patient, top_n=args.top_n)

    # Layer in consistency + GoC
    from synthesis.consistency_check import check_consistency
    from synthesis.goals_of_care import evaluate as eval_goc

    flags = check_consistency(patient)
    report["consistency_flags"] = [{"severity": f.severity, "title": f.title, "detail": f.detail} for f in flags]
    goc = eval_goc(patient)
    report["goals_of_care"] = {
        "triggered": goc.triggered,
        "reasons": goc.reasons,
        "recommendation": goc.recommendation,
    }

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"Decision Report synthesized: {len(report['decision_paths'])} path(s)")
    for p in report["decision_paths"]:
        print(f"  #{p['rank']} {p['trial_id']} ({p['path_type']}) — {p['rationale_one_liner']}")
    if report["consistency_flags"]:
        print(f"  Consistency flags: {len(report['consistency_flags'])}")
    if report["goals_of_care"]["triggered"]:
        print(f"  GoC triggered: {report['goals_of_care']['reasons']}")
