"""
goals_of_care.py — v1.6.0 P2.2

Trigger conditions for adding a "Goals of Care" section to the report:

  - ≥3L treatment failed
  - ECOG ≥ 2
  - Multi-organ failure markers (ALT >5× ULN, CrCl <30, ANC <1.0)
  - PDAC IV + 1L PD in ≤4 cycles (rapid progression suggesting aggressive biology)
  - Age ≥75 or frailty indicators
  - Cancer with median OS <6 months at this line per literature

If triggered, the synthesizer adds a Goals of Care paragraph that runs IN ADDITION TO
(not instead of) the Decision Report Top N. Per ethics + Temel et al. NEJM 2010
(early palliative care extends survival in some metastatic cancer settings).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from data import ontology_loader as ont  # noqa: E402


@dataclass
class GoCTrigger:
    triggered: bool
    reasons: list[str]
    recommendation: str


def evaluate(patient: dict) -> GoCTrigger:
    """
    v1.7.1 — All thresholds come from data/clinical_ontology.json.
    No hard-coded (cancer, line) thresholds remain in code.
    """
    reasons = []

    cancer = patient.get("cancer_type", "")
    cancer_key = ont.normalize_cancer_key(cancer)
    lines = patient.get("treatment_lines_completed", 0)
    ecog = patient.get("ecog", 0)

    # Trigger 1: ≥3L
    if lines >= 3:
        reasons.append(f"已完成 {lines} 线治疗（三线及以上）")

    # Trigger 2: ECOG ≥2
    if ecog >= 2:
        reasons.append(f"ECOG ≥ 2 (患者 {ecog})")

    # Trigger 3: organ failure markers
    labs = patient.get("labs", {})
    if labs.get("alt_x_uln", 0) > 5:
        reasons.append(f"ALT > 5× ULN")
    if labs.get("crcl") and labs["crcl"] < 30:
        reasons.append(f"CrCl < 30")
    if labs.get("anc") and labs["anc"] < 1.0:
        reasons.append(f"ANC < 1.0")

    # Trigger 4: rapid 1L progression — threshold from ontology, applies to ALL cancers
    rapid_cycles_threshold = ont.get_rapid_progression_threshold(cancer)
    if lines == 1:
        for tx in patient.get("treatment_history", []):
            if tx.get("outcome") == "PD" and tx.get("cycles", 0) <= rapid_cycles_threshold:
                reasons.append(
                    f"{cancer_key} 一线快速进展（{tx.get('regimen','化疗')} ≤{tx.get('cycles')}周期 PD，"
                    f"<{rapid_cycles_threshold} cycles 阈值）— 提示侵袭性生物学"
                )
                break

    # Trigger 5: age >=75 or frailty
    age = patient.get("age")
    if age and age >= 75:
        reasons.append(f"年龄 ≥75 ({age} 岁)")
    if patient.get("frailty_indicators"):
        reasons.append(f"虚弱指征: {patient['frailty_indicators']}")

    # Trigger 6: expected OS at current line < cancer-specific threshold
    # v1.7.1: pull from ontology — supports all cancers without code changes
    # Use molecular_subtype if available (NSCLC EGFR-mut has very different OS than KRAS WT)
    molecular_subtype = None
    muts = patient.get("mutations", [])
    if muts:
        # heuristic: first mutation's primary gene (e.g., "EGFR L858R" → "egfr_mut")
        primary_gene = muts[0].split()[0].lower()
        if primary_gene in ("egfr", "alk", "ros1", "ret", "met"):
            molecular_subtype = f"{primary_gene}_mut" if primary_gene == "egfr" else f"{primary_gene}_pos"
        elif "kras g12c" in muts[0].lower():
            molecular_subtype = "kras_g12c"

    expected_os = ont.get_median_os_at_line(cancer, lines, molecular_subtype)
    goc_threshold = ont.get_goc_threshold(cancer)

    if expected_os is not None and expected_os <= goc_threshold:
        reasons.append(
            f"{cancer_key} 当前线（{lines}L 后{f', {molecular_subtype}' if molecular_subtype else ''}）"
            f"文献基线中位 OS ≈ {expected_os} 月（≤ {goc_threshold} 月阈值，建议同时讨论治疗目标）"
        )

    triggered = len(reasons) > 0

    recommendation = ""
    if triggered:
        recommendation = (
            "建议在与主治医生讨论临床试验的**同时**，并行讨论以下问题：\n"
            "1. **治疗目标的优先级**：延长生存 / 维持生活质量 / 兼顾。\n"
            "2. **可承受的不便程度**：国内 vs 跨城市 vs 跨国。海外试验 6-12 个月异地居留 + 高昂非药物成本对家庭的实际负担。\n"
            "3. **缓和医疗（palliative care）介入时机**：注意，现代缓和医疗 ≠ '放弃治疗'，而是 **与抗癌治疗并行**，"
            "用于症状管理（疼痛 / 食欲 / 情绪 / 睡眠）。Temel et al. NEJM 2010 显示早期缓和医疗介入与更好的生存质量相关，且部分场景下与更长的总生存相关。\n"
            "4. **家庭决策代理人 / 医疗指示文件**：在患者状态尚好时讨论，避免危机时刻的家庭分歧。\n\n"
            "**这不是 '治疗失败的备选'，而是与试验入组同等合法的医疗路径**。可在三甲医院的'肿瘤舒缓门诊 / 安宁疗护门诊 / 疼痛门诊'获取。"
        )

    return GoCTrigger(triggered=triggered, reasons=reasons, recommendation=recommendation)
