"""
runner.py — v1.6.0 P2.4

Eval runner for golden test cases.

For each case file in golden_cases/:
  1. Load expected_* fields
  2. (Optionally) run the v1.6.0 pipeline against case.patient_profile
  3. Score each expectation:
     - gating bucket match
     - decision_path inclusion
     - consistency_flag presence
     - GoC triggering

Metrics tracked:
  - precision @ Top 3 (decision report)
  - recall @ match-bucket
  - false-positive count (excluded that shouldn't have been)
  - false-negative count (matched that shouldn't have been)
  - GoC trigger accuracy
  - consistency flag accuracy

Output: eval_report.json + console summary.

Usage:
  python eval/runner.py --gated <gated_v160.json> --case <case_01_pdac_kras_g12d.json>
  python eval/runner.py --pipeline-results-dir results/ --cases-dir eval/golden_cases/
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CaseResult:
    case_id: str
    gating_precision: float = 0.0
    gating_recall: float = 0.0
    gating_false_positives: list[str] = field(default_factory=list)
    gating_false_negatives: list[str] = field(default_factory=list)
    gating_misclassified: list[dict] = field(default_factory=list)
    decision_paths_top3_match: bool = False
    domestic_path_present: bool = False
    consistency_flag_match: bool = False
    goc_match: bool = False
    overall_pass: bool = False
    notes: list[str] = field(default_factory=list)


def evaluate_case(case: dict, gated_data: dict, decision_report: dict) -> CaseResult:
    """
    case: golden test case dict
    gated_data: output of gating + verification + feasibility (gated_v160.json structure)
    decision_report: output of decision_paths.synthesize

    v1.7.1 generalization:
      - must_match_keywords: assert at least one match-bucket trial title/intervention contains keyword
      - domestic_path_present: only fail if the case explicitly requires it
    """
    res = CaseResult(case_id=case.get("case_id"))
    expected = case.get("expected_gating", {})

    actual_match_ids = {t["id"] for t in gated_data.get("match", []) if not _has_cancer_mismatch(t)}
    actual_conditional_ids = {t["id"] for t in gated_data.get("conditional", [])}
    actual_exclude_ids = {t["id"] for t in gated_data.get("exclude", [])}

    must_match = set(expected.get("must_match", []))
    must_conditional = set(expected.get("must_be_conditional", []))
    must_exclude = set(expected.get("must_exclude", []))
    must_match_keywords = expected.get("must_match_keywords", [])

    # Keyword-based match check (for cases where specific NCT IDs are unstable across runs).
    # Semantic: case provides candidate keywords; pass requires AT LEAST ONE match
    # (realistic — different golden runs of the same case may surface different specific drugs).
    # Optionally case can also specify min_keyword_hits (default 1).
    keyword_match_satisfied = True
    if must_match_keywords:
        min_hits = case.get("expected_gating", {}).get("min_keyword_hits", 1)
        match_texts = " ".join(
            (t.get("title", "") + " " + " ".join(t.get("interventions", [])))
            for t in gated_data.get("match", [])
        ).lower()
        hits = [kw for kw in must_match_keywords if kw.lower() in match_texts]
        misses = [kw for kw in must_match_keywords if kw.lower() not in match_texts]
        if len(hits) < min_hits:
            keyword_match_satisfied = False
            res.notes.append(
                f"must_match_keywords: only {len(hits)}/{len(must_match_keywords)} matched "
                f"(need ≥{min_hits}). hits={hits} misses={misses}"
            )
        elif misses:
            res.notes.append(f"must_match_keywords partial: {len(hits)}/{len(must_match_keywords)} matched "
                              f"(>{min_hits} required). missed={misses}")

    # 1. Gating bucket precision/recall
    if must_match:
        match_correct = must_match & actual_match_ids
        match_missed = must_match - actual_match_ids
        # missed but in conditional bucket is partial credit
        match_in_other = match_missed & (actual_conditional_ids | actual_exclude_ids)
        for tid in match_in_other:
            actual_bucket = "conditional" if tid in actual_conditional_ids else "exclude"
            res.gating_misclassified.append({"id": tid, "expected": "match", "actual": actual_bucket})
        res.gating_recall = len(match_correct) / max(1, len(must_match))
    elif must_match_keywords:
        # No NCT IDs specified — use keyword hits as recall proxy
        # (the keyword check handles pass/fail above; here we just give recall a meaningful number)
        match_texts = " ".join(
            (t.get("title", "") + " " + " ".join(t.get("interventions", [])))
            for t in gated_data.get("match", [])
        ).lower()
        kw_hits = sum(1 for kw in must_match_keywords if kw.lower() in match_texts)
        res.gating_recall = kw_hits / len(must_match_keywords)
    else:
        res.gating_recall = 1.0  # nothing to recall against

    if must_exclude:
        excl_correct = must_exclude & (actual_exclude_ids | actual_conditional_ids)  # conditional OK for excludes
        excl_in_match = must_exclude & actual_match_ids
        for tid in excl_in_match:
            res.gating_misclassified.append({"id": tid, "expected": "exclude", "actual": "match"})
            res.gating_false_negatives.append(tid)

    if must_conditional:
        cond_correct = must_conditional & (actual_conditional_ids | actual_match_ids)  # match also OK
        for tid in must_conditional - cond_correct:
            res.gating_misclassified.append({"id": tid, "expected": "conditional", "actual": "exclude" if tid in actual_exclude_ids else "missing"})

    # Precision: of all our match-bucket IDs, how many match the expected (loose: no must_exclude)
    if must_exclude:
        wrongly_matched = actual_match_ids & must_exclude
        res.gating_precision = 1.0 - (len(wrongly_matched) / max(1, len(actual_match_ids)))
    else:
        res.gating_precision = 1.0

    # 2. Decision paths
    expected_dp = case.get("expected_decision_paths", {})
    actual_paths = [p["trial_id"] for p in decision_report.get("decision_paths", [])]
    actual_path_set = set(actual_paths)

    # Backward-compat support: top_3_must_include_one_of (strict positional) OR
    # top_n_must_include_each (any ordering, must include 1 from each group) OR
    # semantic checks (must_have_kras_g12d_path, must_have_alternative_mechanism)
    must_include_pos = expected_dp.get("top_3_must_include_one_of", [])
    must_include_each = expected_dp.get("top_n_must_include_each", [])
    if must_include_pos:
        res.decision_paths_top3_match = all(
            any(opt in actual_path_set for opt in opt_list) for opt_list in must_include_pos
        )
    elif must_include_each:
        res.decision_paths_top3_match = all(
            any(opt in actual_path_set for opt in opt_list) for opt_list in must_include_each
        )
    else:
        res.decision_paths_top3_match = True  # no positional check requested

    # Semantic checks: any path contains a specific risk-key class?
    # Generalized: case can specify any list of risk_keys via must_have_risk_keys
    risk_key_checks = []
    if expected_dp.get("must_have_kras_g12d_path"):
        risk_key_checks.append(("KRAS G12D path", ["kras_g12d_first_in_class", "pan_ras_inhibitor"]))
    if expected_dp.get("must_have_kras_g12c_path"):
        # KRAS G12C drugs aren't in our PDAC-focused taxonomy by default — check by drug name
        # so that the test fires whether or not we have a dedicated risk profile
        present = False
        for p in decision_report.get("decision_paths", []):
            text = (p.get("trial_title", "") + " " + " ".join(
                eff.get("drug", "") if (eff := p.get("efficacy_snapshot")) else ""
                for _ in [None]
            )).lower()
            if any(k in text for k in ["g12c", "calderasib", "adagrasib", "sotorasib", "glecirasib",
                                          "divarasib", "fulzerasib", "mk-1084", "jab-21822"]):
                present = True
                break
        res.decision_paths_top3_match = res.decision_paths_top3_match and present
        if not present:
            res.notes.append("No KRAS G12C-targeted path in Top N")
    if expected_dp.get("must_have_egfr_path"):
        present = False
        for p in decision_report.get("decision_paths", []):
            text = (p.get("trial_title", "") + " " + p.get("rationale_one_liner", "")).lower()
            risks = [r.get("key") for r in p.get("risks", [])]
            if any(k in text for k in ["egfr", "osimertinib", "lazertinib", "amivantamab",
                                          "patritumab", "savolitinib"]) or "egfr_combo_pdac" in risks:
                present = True
                break
        res.decision_paths_top3_match = res.decision_paths_top3_match and present
        if not present:
            res.notes.append("No EGFR-targeted path in Top N")
    if expected_dp.get("must_have_risk_keys"):
        required_keys = expected_dp["must_have_risk_keys"]
        present = False
        for p in decision_report.get("decision_paths", []):
            risks = [r.get("key") for r in p.get("risks", [])]
            if any(rk in risks for rk in required_keys):
                present = True
                break
        res.decision_paths_top3_match = res.decision_paths_top3_match and present
        if not present:
            res.notes.append(f"No path with required risk keys {required_keys} in Top N")

    for label, allowed_risks in risk_key_checks:
        kras_g12d_present = False
        for p in decision_report.get("decision_paths", []):
            risks = [r.get("key") for r in p.get("risks", [])]
            if any(k in risks for k in allowed_risks):
                kras_g12d_present = True
                break
        res.decision_paths_top3_match = res.decision_paths_top3_match and kras_g12d_present
        if not kras_g12d_present:
            res.notes.append(f"No {label} in Top N")

    # Semantic check: alternative mechanism family present?
    if expected_dp.get("must_have_alternative_mechanism"):
        families = set()
        for p in decision_report.get("decision_paths", []):
            risks = [r.get("key") for r in p.get("risks", [])]
            if any(r in risks for r in ["tcr_t_pdac", "car_t_solid_tumor", "til_solid_tumor"]):
                families.add("cell_therapy")
            elif any(r in risks for r in ["claudin18_2_targeted", "msln_targeted"]):
                families.add("adc_or_biologic")
            elif "kras_neoantigen_vaccine" in risks:
                families.add("vaccine")
            elif any(r in risks for r in ["kras_g12d_first_in_class", "pan_ras_inhibitor"]):
                families.add("small_molecule")
        if len(families) < 2:
            res.decision_paths_top3_match = False
            res.notes.append(f"Decision Report lacks mechanism diversity (found families: {families})")

    # Top-1 exact ID check (anchor on the single best path)
    if expected_dp.get("expected_top1_id"):
        if not actual_paths or actual_paths[0] != expected_dp["expected_top1_id"]:
            res.decision_paths_top3_match = False
            res.notes.append(f"Top-1 expected {expected_dp['expected_top1_id']} but got {actual_paths[0] if actual_paths else 'none'}")

    # Always compute domestic_path_present as informational; only enforce when required
    res.domestic_path_present = any(
        (p["sites_in_country"] > 0) for p in decision_report.get("decision_paths", [])
    )
    domestic_required = bool(expected_dp.get("must_have_domestic_path"))

    # 3. Consistency flags
    expected_flags = case.get("expected_consistency_flags", {})
    actual_flags = decision_report.get("consistency_flags", [])
    if "min_count" in expected_flags:
        # Use 'in' check so min_count: 0 is honored (vs falsy 'get(min_count) → 0' path)
        res.consistency_flag_match = len(actual_flags) >= expected_flags["min_count"]
    else:
        res.consistency_flag_match = True
    must_keywords = expected_flags.get("must_include_keywords", [])
    if must_keywords:
        flag_text = " ".join(f.get("title", "") + " " + f.get("detail", "") for f in actual_flags)
        res.consistency_flag_match = res.consistency_flag_match and all(kw in flag_text for kw in must_keywords)

    # 4. GoC
    expected_goc = case.get("expected_goc_triggered", None)
    actual_goc = decision_report.get("goals_of_care", {}).get("triggered", False)
    if expected_goc is not None:
        res.goc_match = (actual_goc == expected_goc)
        # Check reasons if specified
        must_reasons = case.get("expected_goc_reasons_must_include", [])
        if must_reasons and actual_goc:
            actual_reasons_text = " ".join(decision_report.get("goals_of_care", {}).get("reasons", []))
            for r in must_reasons:
                if r not in actual_reasons_text:
                    res.goc_match = False
                    res.notes.append(f"Missing GoC reason: '{r}'")
                    break

    # Overall pass: all dimensions pass
    has_dp_check = bool(must_include_pos or must_include_each
                          or expected_dp.get("must_have_kras_g12d_path")
                          or expected_dp.get("must_have_alternative_mechanism")
                          or expected_dp.get("expected_top1_id"))
    domestic_check_ok = (res.domestic_path_present if domestic_required else True)
    keyword_check_ok = keyword_match_satisfied if must_match_keywords else True

    res.overall_pass = (
        (res.gating_precision >= 0.85)
        and (res.gating_recall >= 0.80 if must_match else True)
        and keyword_check_ok
        and (res.decision_paths_top3_match if has_dp_check else True)
        and domestic_check_ok
        and (res.consistency_flag_match if expected_flags else True)
        and (res.goc_match if expected_goc is not None else True)
    )
    return res


def _has_cancer_mismatch(trial):
    return any("do not include patient cancer" in m for m in trial.get("verification", {}).get("mismatches", []))


def run_one(case_path: Path, gated_path: Path, report_path: Path) -> CaseResult:
    with open(case_path) as f:
        case = json.load(f)
    with open(gated_path) as f:
        gated = json.load(f)
    with open(report_path) as f:
        report = json.load(f)
    return evaluate_case(case, gated, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Single case JSON path")
    parser.add_argument("--gated", help="Gated/scored data JSON")
    parser.add_argument("--report", help="Decision report JSON")
    parser.add_argument("--cases-dir", help="Directory of case JSONs")
    parser.add_argument("--out", default="eval_report.json")
    args = parser.parse_args()

    results = []
    if args.case and args.gated and args.report:
        r = run_one(Path(args.case), Path(args.gated), Path(args.report))
        results.append(r)

    # Print results
    print("\n=== Eval Results ===")
    for r in results:
        status = "✅ PASS" if r.overall_pass else "❌ FAIL"
        print(f"\n{status} | {r.case_id}")
        print(f"  Gating precision: {r.gating_precision:.2f}, recall: {r.gating_recall:.2f}")
        if r.gating_misclassified:
            print(f"  Misclassified ({len(r.gating_misclassified)}):")
            for m in r.gating_misclassified[:5]:
                print(f"    - {m['id']}: expected={m['expected']}, actual={m['actual']}")
        print(f"  Decision Paths Top-3 inclusion: {r.decision_paths_top3_match}")
        print(f"  Domestic path present: {r.domestic_path_present}")
        print(f"  Consistency flags match: {r.consistency_flag_match}")
        print(f"  GoC match: {r.goc_match}")
        if r.notes:
            print(f"  Notes: {r.notes}")

    # Aggregate metrics
    if len(results) > 1:
        n_pass = sum(1 for r in results if r.overall_pass)
        avg_prec = sum(r.gating_precision for r in results) / len(results)
        avg_rec = sum(r.gating_recall for r in results) / len(results)
        print(f"\n=== Summary: {n_pass}/{len(results)} passed | avg precision {avg_prec:.2f} | avg recall {avg_rec:.2f}")

    # Save
    with open(args.out, "w") as f:
        json.dump({"results": [r.__dict__ for r in results]}, f, indent=2, default=lambda x: x.__dict__ if hasattr(x, '__dict__') else str(x))


if __name__ == "__main__":
    main()
