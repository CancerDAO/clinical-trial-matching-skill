"""
consistency_check.py — v1.6.0 P2.1

Patient profile internal-consistency check. Flags clinically tense
combinations that v1.5.0 silently propagated as "favorable patient":

  - Recent (≤4 weeks) chemotherapy + ECOG 0 + no documented residual toxicity
  - Stage IV multi-organ mets + "no comorbidity" (likely under-documented)
  - Quick PD on systemic therapy (≤3 cycles) + healthy organ-function description
  - Age unknown
  - Affordability_tier high without documented basis

Output: list of CaveatFlags. Decision Report header carries the warning banner
when any flag fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaveatFlag:
    severity: str  # "info" | "warn" | "danger"
    title: str
    detail: str


def check_consistency(patient: dict) -> list[CaveatFlag]:
    flags = []

    # 1. Recent chemo + ECOG 0 + no residual toxicity
    last_dose = None
    for tx in patient.get("treatment_history", []):
        if tx.get("last_dose_date"):
            last_dose = tx["last_dose_date"]
            break
    ecog = patient.get("ecog", 1)
    no_residual = patient.get("organ_function") == "normal" and not patient.get("comorbidities", [])

    if last_dose is None and patient.get("treatment_lines_completed", 0) >= 1 and ecog == 0 and no_residual:
        flags.append(CaveatFlag(
            severity="warn",
            title="Recent chemotherapy + ECOG 0 + no residual toxicity documented",
            detail="患者刚完成 ≥1L 化疗即报告 ECOG 0 且无残余毒性 — 临床上较少见。FOLFIRINOX / AG 通常会留下神经病变 / 骨髓抑制 / 体重下降 / 疲劳。建议在筛选前补充：(1) 最末次给药日期；(2) CTCAE 残余毒性等级；(3) 体重变化曲线。在补全前，建议把 feasibility timeline 视为偏乐观估计。"
        ))

    # 2. Stage IV multi-organ + no comorbidity
    mets = patient.get("metastasis_sites", [])
    if patient.get("stage", "").upper() == "IV" and len(mets) >= 2 and not patient.get("comorbidities", []):
        flags.append(CaveatFlag(
            severity="info",
            title=f"IV 期多脏器转移 ({mets}) + 报告无合并症",
            detail="多脏器转移患者通常会因肿瘤负荷出现 ALT/AST 升高、低白蛋白、轻度贫血、CRP 升高。'无合并症' 的描述可能反映文档不全（而非真实健康）。建议补具体的 ALT/AST/Tbil/Alb/Hb/ANC/CrCl 数值。"
        ))

    # 3. Quick PD on systemic therapy
    for tx in patient.get("treatment_history", []):
        if tx.get("outcome") == "PD" and tx.get("cycles", 0) <= 3:
            flags.append(CaveatFlag(
                severity="warn",
                title=f"治疗 ≤3 周期即进展 ({tx.get('regimen','?')} {tx.get('cycles','?')}周期 PD)",
                detail="标准化疗方案在 ≤3 周期内进展提示侵袭性疾病或耐药。CR/PR/SD 的中位时间通常 ≥2 个月。建议确认：(1) 进展是影像学还是临床；(2) 病灶倍增时间；(3) CA19-9 / 其他标志物动力学；(4) 是否需要紧急姑息治疗（疼痛 / 黄疸）。"
            ))

    # 4. Age unknown
    if patient.get("age") is None:
        flags.append(CaveatFlag(
            severity="info",
            title="年龄未记录",
            detail="多数试验有年龄上限（通常 ≤75 岁，部分 ≤70）。建议在筛选前补充。"
        ))

    # 5. Affordability tier high without basis
    tier = patient.get("affordability_tier")
    if tier == "high" and "documented_basis" not in patient:
        flags.append(CaveatFlag(
            severity="info",
            title="affordability_tier=high 未记录依据",
            detail="海外试验非药物费用通常 8-15 万 USD/年。如未确认家庭支付能力，'高 tier' 可能高估。建议保持 medium 或与家属直接确认。"
        ))

    # 6. ECOG 0 + multi-organ mets specifically
    if ecog == 0 and len(mets) >= 2 and patient.get("treatment_lines_completed", 0) >= 1:
        flags.append(CaveatFlag(
            severity="info",
            title="ECOG 0 + 多脏器转移 + 已 1L 进展 — 罕见组合",
            detail="ECOG 0 = 完全活动无症状。多脏器转移 + 1L 进展患者通常会有疲劳 / 厌食 / 体重下降 (ECOG 1)。如医生评估真为 0，是不多见的'仍能去海外试验'状态；但建议在筛选时由研究中心 PI 重新评估。"
        ))

    return flags


def consistency_summary_html(flags: list[CaveatFlag]) -> str:
    if not flags:
        return ""
    rows = []
    for f in flags:
        cls = {"info": "badge-info", "warn": "badge-warn", "danger": "badge-danger"}.get(f.severity, "badge-info")
        rows.append(f'<div class="gap-item"><span class="badge {cls}">{f.severity.upper()}</span><div class="gap-content"><div class="gap-title">{f.title}</div><div class="gap-desc">{f.detail}</div></div></div>')
    return "\n".join(rows)
