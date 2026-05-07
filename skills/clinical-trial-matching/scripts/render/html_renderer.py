"""
html_renderer.py — v2.0.0

Render the Decision Report (with Match List as collapsible second layer) as
a single self-contained HTML file. No external dependencies.

v2 changes:
- Removed hardcoded "PDAC" / cancer-type-specific labels in efficacy block;
  cancer type is read from patient profile dynamically.
- Accepts decision-synthesizer's canonical output schema for
  alternatives_comparison ({alternative_id, alternative_title, why_picked_won})
  AND its alias schema ({trial_id, trial_title, reason_not_chosen}) for
  backward compatibility.
- Same flexible-key handling for consistency_flags ({title, detail} OR
  {flag, evidence}).
- Null-safe handling of `verification` field (ChiCTR trials lack NCT-API
  verification but should still render).
- Null-safe `trial_id` (decision_paths can include "continue current SoC"
  with no trial reference).
"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any


def _safe(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def _trial_url(trial_id: Any) -> str:
    if not trial_id or not isinstance(trial_id, str):
        return "#"
    if trial_id.startswith("NCT"):
        return f"https://clinicaltrials.gov/study/{trial_id}"
    if trial_id.startswith("ChiCTR"):
        return f"https://www.chictr.org.cn/showproj.html?proj={trial_id}"
    return "#"


def _verification(t: dict) -> dict:
    """Null-safe accessor for trial.verification — ChiCTR/skipped entries return {}."""
    v = t.get("verification") if isinstance(t, dict) else None
    return v if isinstance(v, dict) else {}


def render_decision_path(p: dict) -> str:
    rank = p.get("rank", "?")
    # v2 schema uses `role`; v1.7 used `path_type`. Accept either.
    path_kind = p.get("path_type") or p.get("role") or "secondary"
    badge = {
        "primary": '<span class="badge badge-good">主推路径</span>',
        "primary_overseas": '<span class="badge badge-good">主推 · 海外</span>',
        "secondary": '<span class="badge badge-info">备选路径</span>',
        "secondary_overseas": '<span class="badge badge-info">备选 · 海外</span>',
        "secondary_cell_therapy": '<span class="badge badge-info">备选 · 细胞治疗</span>',
        "bridging": '<span class="badge badge-warn">桥接 / Plan B</span>',
        "fallback": '<span class="badge badge-warn">回退方案</span>',
    }.get(path_kind, '<span class="badge badge-info">备选路径</span>')

    eff = p.get("efficacy_snapshot") or {}
    eff_metrics = eff.get("metrics", {}) if eff else {}
    vs_soc = p.get("vs_soc") or {}

    # Feasibility: schema spec stores flat `feasibility_score` + `feasibility_dims`;
    # legacy v1.7 stored a nested dict at `feasibility`. Accept both.
    feas = p.get("feasibility") or {}
    if not feas and "feasibility_score" in p:
        feas = {
            "composite": p.get("feasibility_score"),
            "sub_scores": p.get("feasibility_dims") or {},
            "flags": p.get("feasibility_flags") or [],
        }
    sub = feas.get("sub_scores") or {}

    risks = p.get("risks") or []
    timeline = p.get("timeline") or p.get("estimated_timeline") or {}

    # Blockers: spec uses flat `blockers_satisfied` / `blockers_pending`;
    # v1.7 used nested `blockers_status: {satisfied: [...], pending: [...]}`. Accept both.
    blockers = p.get("blockers_status") or {}
    if not blockers and ("blockers_satisfied" in p or "blockers_pending" in p):
        blockers = {
            "satisfied": p.get("blockers_satisfied") or [],
            "pending": p.get("blockers_pending") or [],
            "advisors_unknown": p.get("advisors_unknown") or [],
        }

    # Efficacy snapshot — v1.7: distinct visual style for estimate vs real data
    eff_html = ""
    if eff:
        match_type = eff.get("match_type", "")
        if match_type in ("exact_nct", "trial_specific"):
            # Accept the synthesizer's canonical key names AND legacy aliases.
            orr = (eff_metrics.get("orr")
                   or eff_metrics.get("expected_orr")
                   or eff_metrics.get("orr_pdac"))  # legacy fallback
            pfs = (eff_metrics.get("median_pfs_months")
                   or eff_metrics.get("expected_pfs_months")
                   or eff_metrics.get("median_pfs_months_pdac"))  # legacy fallback
            os_mo = eff_metrics.get("median_os_months") or eff_metrics.get("expected_os_months")
            src = eff.get("source", {})
            eff_html = f"""
            <div class="eff-block eff-real">
                <div class="eff-title">📊 疗效快照（<strong>NCT-level 真实数据</strong>）</div>
                <ul>
                    <li>ORR: <strong>{f'{int(orr*100)}%' if isinstance(orr,(int,float)) else _safe(orr or 'N/A')}</strong>{f' ({_safe(eff_metrics.get("orr_ci",""))})' if eff_metrics.get('orr_ci') else ''}</li>
                    <li>mPFS: <strong>{_safe(pfs or 'N/A')} 月</strong></li>
                    <li>mOS: <strong>{_safe(os_mo or eff_metrics.get('os_note','未达到'))}</strong></li>
                    <li>数据成熟度: <em>{_safe(eff.get('maturity','?'))}</em></li>
                    <li>来源: {_safe(src.get('citation','?'))} <span class="muted">({_safe(src.get('venue',''))})</span></li>
                </ul>
                <div class="caveat">{_safe(eff.get('caveats',''))}</div>
            </div>"""
        elif match_type in ("drug_class_baseline", "mutation_class_baseline"):
            cls = eff.get("drug_class") or eff.get("class") or "?"
            m = eff_metrics
            cancer_label = _safe(eff.get("cancer_context") or "该癌种")
            # Accept canonical (expected_orr / expected_orr_range) AND legacy
            # cancer-specific keys (expected_orr_2L_pdac, expected_orr_2L_crc, ...)
            expected_orr = (m.get("expected_orr_range")
                            or m.get("expected_orr")
                            or next((m[k] for k in m if k.startswith("expected_orr_")), "N/A"))
            expected_pfs = m.get("expected_pfs_range") or m.get("expected_pfs_months", "N/A")
            eff_html = f"""
            <div class="eff-block eff-estimate">
                <div class="eff-banner">⚠️ 以下为<strong>机制类基线估算</strong>，<u>非该试验真实数据</u> — 实际 ORR 可能显著高于或低于此区间</div>
                <div class="eff-title">疗效估算（机制类）</div>
                <ul>
                    <li>机制类: <strong>{_safe(cls)}</strong></li>
                    <li>预期 ORR ({cancer_label}): <strong>{_safe(expected_orr)}</strong> <span class="muted">(机制类公开数据中位数)</span></li>
                    <li>预期 mPFS: <strong>{_safe(expected_pfs)} 月</strong></li>
                </ul>
                <div class="caveat">{_safe(eff.get('caveats') or f'试验级数据未发布；以上为该机制类在{cancer_label}的公开基线估算。')}</div>
            </div>"""
        elif match_type == "drug_match":
            orr = (eff_metrics.get("orr")
                   or eff_metrics.get("expected_orr")
                   or eff_metrics.get("orr_pdac"))  # legacy
            pfs = (eff_metrics.get("median_pfs_months")
                   or eff_metrics.get("expected_pfs_months")
                   or eff_metrics.get("median_pfs_months_pdac"))  # legacy
            src = eff.get("source", {})
            eff_html = f"""
            <div class="eff-block eff-drug-match">
                <div class="eff-banner-info">ℹ️ 以下为<strong>同药物的其它试验数据</strong>（非本试验本身）</div>
                <div class="eff-title">疗效参考（同药物公开数据）</div>
                <ul>
                    <li>ORR (相关试验): <strong>{f'{int(orr*100)}%' if isinstance(orr,(int,float)) else _safe(orr or 'N/A')}</strong></li>
                    <li>mPFS (相关试验): <strong>{_safe(pfs or 'N/A')} 月</strong></li>
                    <li>来源: {_safe(src.get('citation','?'))}</li>
                </ul>
                <div class="caveat">{_safe(eff.get('caveats',''))}</div>
            </div>"""
        else:
            eff_html = '<div class="eff-block eff-no-data"><em>该试验暂无可用疗效数据快照（待筛选时与研究中心确认）</em></div>'
    else:
        eff_html = '<div class="eff-block eff-no-data"><em>未匹配到该试验或机制类的疗效数据</em></div>'

    # vs SoC
    soc_html = ""
    if vs_soc.get("available"):
        deltas = vs_soc.get("deltas", {})
        delta_orr = deltas.get("orr_delta")
        delta_str = ""
        if isinstance(delta_orr, (int, float)):
            sign = "+" if delta_orr > 0 else ""
            delta_str = f' <span class="badge badge-good">⊕ {sign}{int(delta_orr*100)}% ORR</span>'
        soc_html = f"""
        <div class="soc-block">
            <div class="eff-title">vs 标准治疗（{_safe(vs_soc.get('soc_regimen','?'))}）{delta_str}</div>
            <table class="soc-table">
                <tr><th>指标</th><th>试验</th><th>SoC</th></tr>
                <tr><td>ORR</td><td>{_safe(vs_soc.get('trial_orr','—'))}</td><td>{_safe(vs_soc.get('soc_orr','—'))}</td></tr>
                <tr><td>mPFS (月)</td><td>{_safe(vs_soc.get('trial_median_pfs','—'))}</td><td>{_safe(vs_soc.get('soc_median_pfs','—'))}</td></tr>
                <tr><td>mOS (月)</td><td>—</td><td>{_safe(vs_soc.get('soc_median_os','—'))}</td></tr>
            </table>
            <div class="caveat">SoC pivotal: {_safe(vs_soc.get('soc_pivotal','?'))}{f' ({_safe(vs_soc.get("soc_caveats",""))})' if vs_soc.get('soc_caveats') else ''}</div>
        </div>"""

    # Feasibility radar
    feas_html = f"""
    <div class="feas-block">
        <div class="eff-title">现实可行性 (composite {feas.get('composite','—')})</div>
        <div class="feas-bars">
            {''.join(f'<div class="feas-bar"><span class="feas-label">{_safe(k)}</span><div class="feas-track"><div class="feas-fill" style="width:{int((v if isinstance(v,(int,float)) else 0)*100)}%"></div></div><span class="feas-val">{v}</span></div>' for k, v in sub.items())}
        </div>
        {f'<div class="caveat">⚠️ {len(feas.get("flags",[]))} 个风险标记: ' + '; '.join(_safe(x) for x in feas.get('flags',[])[:5]) + '</div>' if feas.get('flags') else ''}
    </div>"""

    # Risks. Accept canonical (`narrative` per trial-risk-annotator schema) and
    # legacy (`notes`). Render up to first 3 bullets per risk.
    risk_html = ""
    if risks:
        risk_items = []
        for r in risks:
            level = r.get("risk_level") or r.get("level") or ""
            level_class = {
                "high": "badge-danger",
                "high_uncertainty": "badge-danger",
                "high_logistical": "badge-danger",
                "moderate": "badge-warn",
                "moderate_uncertainty": "badge-warn",
                "biomarker_dependent": "badge-warn",
                "experimental": "badge-warn",
                "low": "badge-info",
            }.get(level, "badge-info")
            bullets = r.get("narrative") or r.get("notes") or []
            if isinstance(bullets, str):
                bullets = [bullets]
            notes_html = "<ul>" + "".join(f"<li>{_safe(n)}</li>" for n in bullets[:3]) + "</ul>"
            mech_label = r.get("mechanism") or r.get("key") or "?"
            risk_items.append(
                f'<div class="risk-item"><strong>{_safe(mech_label)}</strong> '
                f'<span class="badge {level_class}">{_safe(level)}</span>{notes_html}</div>'
            )
        risk_html = f'<div class="risk-block"><div class="eff-title">风险标记</div>{"".join(risk_items)}</div>'

    # Timeline. Schema spec uses `screening_window`, `earliest_first_dose`,
    # `critical_path_steps` (list); legacy used `expected_first_dose`,
    # `critical_path` (string). Accept both.
    cp = timeline.get("critical_path_steps") or timeline.get("critical_path")
    cp_str = "；".join(_safe(s) for s in cp) if isinstance(cp, list) else _safe(cp or "?")
    tl_html = f"""
    <div class="timeline-block">
        <div class="eff-title">时间表</div>
        <ul>
            <li>筛选窗口: <strong>{_safe(timeline.get('screening_window','?'))}</strong></li>
            <li>预计首次给药: <strong>{_safe(timeline.get('earliest_first_dose') or timeline.get('expected_first_dose') or '?')}</strong></li>
            <li>关键路径: {cp_str}</li>
        </ul>
    </div>"""

    # Blockers
    blockers_html = ""
    if blockers.get("satisfied") or blockers.get("pending") or blockers.get("advisors_unknown"):
        sat = "".join(f'<li class="crit-met">✅ {_safe(s)}</li>' for s in blockers.get("satisfied", []))
        pend = "".join(f'<li class="crit-warn">⏳ {_safe(s)}</li>' for s in blockers.get("pending", []))
        adv = "".join(f'<li class="crit-unknown">❓ {_safe(s)}</li>' for s in blockers.get("advisors_unknown", []))
        blockers_html = f"""
        <div class="blockers-block">
            <div class="eff-title">硬性条件状态</div>
            <ul>{sat}{pend}{adv}</ul>
        </div>"""

    # v1.7 — Phase 3 randomization + chemo overlap + targeted-class overlap flags
    v17 = p.get("v17_flags", {})
    flag_banners = []
    if v17.get("phase3_risk", {}).get("flag"):
        flag_banners.append(f'<div class="warn-banner">⚠️ <strong>Phase 3 RCT 提示:</strong> {_safe(v17["phase3_risk"]["note"])}</div>')
    if v17.get("chemo_overlap", {}).get("penalty", 0) > 0:
        flag_banners.append(f'<div class="warn-banner">⚠️ <strong>化疗骨架重叠:</strong> {_safe(v17["chemo_overlap"]["reason"])}</div>')
    if v17.get("targeted_overlap", {}).get("penalty", 0) > 0:
        flag_banners.append(f'<div class="warn-banner">⚠️ <strong>同类靶向重复:</strong> {_safe(v17["targeted_overlap"]["reason"])}</div>')
    flags_html = "".join(flag_banners)

    # v1.7+v2 — Alternatives comparison.
    # Accept canonical ({alternative_id, alternative_title, why_picked_won})
    # and synthesizer-emitted aliases ({trial_id, trial_title, reason_not_chosen}).
    alts = p.get("alternatives_comparison", []) or []
    alts_html = ""
    if alts:
        alt_items = []
        for alt in alts:
            alt_id = alt.get("alternative_id") or alt.get("trial_id") or ""
            alt_title = alt.get("alternative_title") or alt.get("trial_title") or ""
            reasons = alt.get("why_picked_won")
            if reasons is None:
                # `reason_not_chosen` is a single string; wrap to list for uniform render
                rnc = alt.get("reason_not_chosen")
                reasons = [rnc] if rnc else []
            reasons_str = "；".join(_safe(r) for r in reasons)
            alt_items.append(
                f'<li><a class="badge-nct" href="{_trial_url(alt_id)}" target="_blank">{_safe(alt_id)}</a> '
                f'<span class="muted">({_safe(alt_title)})</span> — 原因: {reasons_str}</li>'
            )
        alts_html = f"""
        <div class="alts-block">
            <div class="eff-title">为什么选这条 ≠ 选其他相似试验</div>
            <ul>{''.join(alt_items)}</ul>
        </div>"""

    # v1.7 — Consequences of skipping
    consequences = p.get("consequences_of_skipping", "")
    cons_html = ""
    if consequences:
        cons_html = f"""
        <div class="cons-block">
            <div class="eff-title">如果不走这条会怎样？</div>
            <p style="margin:0;font-size:13px;line-height:1.7">{_safe(consequences)}</p>
        </div>"""

    # v2: trial_id may be missing for non-trial paths (e.g. "continue current SoC")
    trial_id = p.get("trial_id")
    trial_title = p.get("trial_title") or p.get("title") or ""
    if trial_id and isinstance(trial_id, str) and (trial_id.startswith("NCT") or trial_id.startswith("ChiCTR")):
        trial_id_html = f'<a class="badge-nct" href="{_trial_url(trial_id)}" target="_blank">{_safe(trial_id)}</a>'
    else:
        trial_id_html = f'<span class="badge badge-info">{_safe(trial_id or "无关联试验")}</span>'

    return f"""
    <div class="path-card path-{path_kind}">
        <div class="path-header">
            <div class="path-rank">#{rank}</div>
            <div class="path-meta">
                {badge}
                <div class="path-trial-id">{trial_id_html} · {_safe(p.get('phase','?'))} · {_safe(p.get('sponsor',''))}</div>
                <div class="path-trial-title">{_safe(trial_title)}</div>
            </div>
        </div>
        {flags_html}
        <div class="path-rationale"><strong>核心理由:</strong> {_safe(p.get('rationale_one_liner') or p.get('rationale') or '')}</div>
        <div class="path-detail">{_safe(p.get('rationale_detailed') or '').replace(chr(10), '<br/>')}</div>
        {eff_html}
        {soc_html}
        {feas_html}
        {alts_html}
        {cons_html}
        {risk_html}
        {tl_html}
        {blockers_html}
    </div>"""


def render_match_list_row(t: dict, bucket: str) -> str:
    md = t.get("metadata", {})
    fs = t.get("feasibility", {})
    gating = t.get("gating", {})
    cn = t.get("china_site_count", 0)
    eff = t.get("efficacy") or {}

    bucket_badge = {
        "match": '<span class="badge badge-good">高匹配</span>',
        "conditional": '<span class="badge badge-warn">条件匹配</span>',
        "exclude": '<span class="badge badge-danger">已排除</span>',
    }.get(bucket, "")

    reason = " ".join(gating.get("reasons", [])[:2])[:160]

    return f"""
    <tr>
        <td>{bucket_badge}</td>
        <td><a class="badge-nct" href="{_trial_url(t['id'])}" target="_blank">{_safe(t['id'])}</a></td>
        <td>{_safe(t.get('title','')[:120])}</td>
        <td>{_safe('/'.join(t.get('phases',[])))}</td>
        <td>{cn}</td>
        <td>{fs.get('composite','—') if fs else '—'}</td>
        <td>{_safe(reason)}</td>
    </tr>"""


def render_html(report: dict, gated_data: dict, patient: dict, output_path: str):
    paths = report.get("decision_paths", [])
    soc = report.get("soc_benchmarks", [])
    consistency = report.get("consistency_flags", [])
    goc = report.get("goals_of_care", {})
    diagnostic = report.get("diagnostic", "")

    # Patient summary cards
    pat_cards = f"""
    <div class="grid">
        <div class="info"><div class="label">癌种</div><div class="value">{_safe(patient.get('cancer_type','?'))}</div></div>
        <div class="info"><div class="label">分期 / 转移</div><div class="value">{_safe(patient.get('stage','?'))} 期 ({_safe(', '.join(patient.get('metastasis_sites',[])))} 转移)</div></div>
        <div class="info"><div class="label">分子特征</div><div class="value">{''.join(f'<span class="mol-tag">{_safe(m)}</span>' for m in patient.get('mutations',[]))}</div></div>
        <div class="info"><div class="label">治疗线数</div><div class="value">{patient.get('treatment_lines_completed',0)} 线 ({_safe(', '.join([t['regimen'] for t in patient.get('treatment_history',[])]))})</div></div>
        <div class="info"><div class="label">ECOG</div><div class="value">{patient.get('ecog','?')}</div></div>
        <div class="info"><div class="label">器官功能 / 合并症</div><div class="value">{_safe(patient.get('organ_function','?'))} / {_safe(', '.join(patient.get('comorbidities',[])) or '无')}</div></div>
        <div class="info"><div class="label">行动力</div><div class="value">{'✅ 可立即出行' if patient.get('willing_to_travel_internationally') else '国内为主'}; affordability_tier: {_safe(patient.get('affordability_tier','medium'))}</div></div>
        <div class="info"><div class="label">已知缺口</div><div class="value">{'HLA: ' + ('已分型' if patient.get('hla_typed') else '❌待补') + ' / CNS MRI: ' + ('✅' if patient.get('cns_imaging_done') else '❌待补') + ' / 病毒筛查: ' + ('✅' if patient.get('viral_serology_done') else '❌待补')}</div></div>
    </div>"""

    # Decision paths section
    paths_html = "\n".join(render_decision_path(p) for p in paths) if paths else '<div class="warn-banner">⚠️ 无路径通过 feasibility + diversity 阈值。请见下方完整匹配清单。</div>'

    # Consistency flags banner.
    # Accept canonical ({severity, title, detail}) and synthesizer-emitted
    # alias ({severity, flag, evidence}). "alert" severity maps to danger.
    consistency_html = ""
    if consistency:
        flag_rows = []
        sev_class = {
            "info": "badge-info",
            "warn": "badge-warn",
            "warning": "badge-warn",
            "danger": "badge-danger",
            "alert": "badge-danger",
            "critical": "badge-danger",
        }
        for f in consistency:
            sev = (f.get("severity") or "info").lower()
            cls = sev_class.get(sev, "badge-info")
            title = f.get("title") or f.get("flag") or ""
            detail = f.get("detail") or f.get("evidence") or ""
            flag_rows.append(
                f'<div class="flag-row"><span class="badge {cls}">{_safe(sev.upper())}</span>'
                f'<div><strong>{_safe(title)}</strong><div class="muted">{_safe(detail)}</div></div></div>'
            )
        consistency_html = f'<div class="consistency-banner"><div class="banner-title">⚠️ 患者画像一致性提示 ({len(consistency)} 项)</div>{"".join(flag_rows)}</div>'

    # GoC section
    goc_html = ""
    if goc.get("triggered"):
        reasons_list = "".join(f"<li>{_safe(r)}</li>" for r in goc.get("reasons", []))
        goc_html = f"""
        <div class="goc-block">
            <div class="banner-title">治疗目标讨论（Goals of Care）</div>
            <p>本次评估触发了以下条件，建议在与主治医生讨论临床试验的<strong>同时</strong>，并行讨论治疗目标：</p>
            <ul>{reasons_list}</ul>
            <div class="goc-recommendation">{_safe(goc.get('recommendation','')).replace(chr(10), '<br/>')}</div>
        </div>"""

    # SoC list
    soc_html = ""
    if soc:
        soc_rows = "".join(f"""<tr><td>{_safe(s.get('regimen','?'))}</td><td>{_safe(s.get('median_os_months', s.get('median_os_months_estimate','—')))}</td><td>{_safe(s.get('orr', s.get('orr_estimate','—')))}</td><td>{_safe(s.get('median_pfs_months', s.get('median_pfs_months_estimate','—')))}</td><td>{_safe(s.get('pivotal','—'))}</td></tr>""" for s in soc)
        soc_html = f"""
        <section class="section">
            <div class="title">标准治疗对照（{_safe(patient.get('cancer_type','?'))} 当前线）</div>
            <table>
                <tr><th>方案</th><th>mOS (月)</th><th>ORR</th><th>mPFS (月)</th><th>关键试验</th></tr>
                {soc_rows}
            </table>
        </section>"""

    # Match list (collapsible)
    match_buckets = {
        "match": gated_data.get("match", []),
        "conditional": gated_data.get("conditional", []),
        "exclude": gated_data.get("exclude", []),
    }
    match_rows = ""
    for bucket, trials in match_buckets.items():
        for t in sorted(trials, key=lambda x: -(x.get('feasibility', {}).get('composite', 0) if isinstance(x.get('feasibility'), dict) else 0))[:30]:
            match_rows += render_match_list_row(t, bucket)

    match_html = f"""
    <details class="match-list">
        <summary>📋 完整匹配清单（{len(match_buckets['match'])} 高匹配 / {len(match_buckets['conditional'])} 条件匹配 / {len(match_buckets['exclude'])} 已排除）— 点击展开</summary>
        <table class="match-table">
            <tr><th>等级</th><th>NCT</th><th>试验</th><th>期别</th><th>CN</th><th>可行</th><th>判定理由</th></tr>
            {match_rows}
        </table>
    </details>"""

    # Diagnostic
    diag_html = f'<div class="diagnostic">{_safe(diagnostic)}</div>' if diagnostic else ""

    # CSS (compact, single-file)
    css = """
        :root { --bg:#f7f4ee; --paper:#fffdfa; --text:#20261f; --muted:#667064; --line:#ddd6c8; --brand:#245c4b; --accent:#c77d1c; --good:#2d6a4f; --warn:#b45309; --danger:#b91c1c; --info:#1e5a8a; }
        * { box-sizing:border-box; }
        body { margin:0; font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; background:linear-gradient(180deg,#f4efe5,#eef4ee); color:var(--text); padding:24px; }
        .page { max-width:1200px; margin:0 auto; background:var(--paper); border:1px solid var(--line); border-radius:20px; overflow:hidden; box-shadow:0 24px 60px rgba(65,55,39,0.08); }
        header { background:linear-gradient(135deg,var(--brand),#3a7a66); color:#fff; padding:24px 32px; display:flex; justify-content:space-between; gap:20px; }
        h1 { margin:0 0 6px; font-size:28px; }
        .hero-sub { font-size:13px; opacity:.95; }
        .meta { font-size:13px; opacity:.95; line-height:1.6; }
        .section { padding:20px 32px; border-top:1px solid var(--line); }
        .title { font-size:18px; font-weight:700; margin-bottom:14px; color:var(--brand); }
        .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
        .info { background:#faf8f2; border:1px solid var(--line); border-radius:12px; padding:12px; }
        .label { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px; }
        .value { font-size:14px; font-weight:600; line-height:1.5; }
        .badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:11px; font-weight:700; }
        .badge-good { background:#e8f4ec; color:var(--good); }
        .badge-warn { background:#fff4e6; color:var(--warn); }
        .badge-danger { background:#fde8e8; color:var(--danger); }
        .badge-info { background:#e0eef8; color:var(--info); }
        .badge-nct { display:inline-block; background:#1a5e3a; color:#fff; padding:2px 8px; border-radius:8px; font-size:11px; font-weight:700; text-decoration:none; }
        .mol-tag { display:inline-block; background:#e8f4ec; color:var(--good); padding:2px 7px; border-radius:6px; font-size:11px; font-weight:600; margin:2px 3px 2px 0; }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        th, td { border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; }
        th { background:#f5f1e7; color:var(--brand); font-weight:700; }

        /* Decision Path Card */
        .path-card { border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin-bottom:16px; background:#fafaf6; }
        .path-card.path-primary { border-left:6px solid var(--good); }
        .path-card.path-primary_overseas { border-left:6px solid var(--accent); }
        .path-card.path-secondary { border-left:6px solid var(--info); }
        .path-card.path-secondary_overseas { border-left:6px solid var(--accent); }
        .path-card.path-secondary_cell_therapy { border-left:6px solid #8b3aa5; }
        .path-header { display:flex; gap:14px; align-items:flex-start; margin-bottom:10px; }
        .path-rank { font-size:34px; font-weight:800; color:var(--brand); width:48px; }
        .path-meta { flex:1; }
        .path-trial-id { font-size:12px; color:var(--muted); margin:4px 0; }
        .path-trial-title { font-size:15px; font-weight:600; line-height:1.4; }
        .path-rationale { font-size:13px; margin-bottom:6px; }
        .path-detail { font-size:12px; color:var(--muted); margin-bottom:12px; line-height:1.6; }

        .eff-block, .soc-block, .feas-block, .risk-block, .timeline-block, .blockers-block,
        .alts-block, .cons-block {
            background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-top:10px; font-size:13px;
        }

        /* v1.7 — Distinct visual style for real vs estimated efficacy data */
        .eff-real { background:#f0f7f4; border-left:5px solid var(--good); }
        .eff-estimate { background:#fff8e6; border-left:5px solid var(--warn); }
        .eff-drug-match { background:#eef5fb; border-left:5px solid var(--info); }
        .eff-no-data { background:#f5f5f0; border-left:5px solid var(--muted); }
        .eff-banner { background:#fef3d7; border-radius:6px; padding:8px 10px; font-size:12px; color:var(--warn); font-weight:600; margin-bottom:10px; }
        .eff-banner-info { background:#e0eef8; border-radius:6px; padding:8px 10px; font-size:12px; color:var(--info); font-weight:600; margin-bottom:10px; }
        .eff-class { background:#fffaf2; }
        .eff-title { font-weight:700; color:var(--brand); margin-bottom:8px; font-size:13px; }
        .caveat { font-size:11px; color:var(--muted); margin-top:6px; line-height:1.5; }

        /* v1.7 — Alternatives + Consequences */
        .alts-block { background:#f7f4ee; border-left:4px solid var(--info); }
        .alts-block ul { margin:6px 0 0; padding-left:18px; font-size:12px; }
        .alts-block li { margin-bottom:6px; line-height:1.6; }
        .cons-block { background:#fdf6f6; border-left:4px solid var(--accent); }

        .feas-bars { display:flex; flex-direction:column; gap:4px; }
        .feas-bar { display:flex; align-items:center; gap:8px; font-size:12px; }
        .feas-label { width:140px; color:var(--muted); }
        .feas-track { flex:1; background:#eee; height:8px; border-radius:4px; overflow:hidden; }
        .feas-fill { height:100%; background:linear-gradient(90deg,#a8c8a4,#2d6a4f); }
        .feas-val { width:40px; text-align:right; font-weight:600; }

        .risk-item { padding:6px 0; border-bottom:1px solid #f0ece3; }
        .risk-item:last-child { border-bottom:none; }
        .risk-item ul { margin:4px 0 0; padding-left:18px; font-size:12px; color:var(--muted); }

        .soc-table th { font-size:11px; padding:5px 8px; }
        .soc-table td { font-size:12px; padding:5px 8px; }

        .crit-met { color:var(--good); }
        .crit-warn { color:var(--warn); }
        .crit-unknown { color:var(--muted); }

        .consistency-banner { background:#fff8e6; border:1px solid #f0dca0; border-radius:12px; padding:14px 18px; margin:20px 0; }
        .banner-title { font-size:14px; font-weight:700; color:var(--warn); margin-bottom:10px; }
        .flag-row { display:flex; gap:10px; margin-bottom:8px; align-items:flex-start; }
        .flag-row .muted { font-size:11px; color:var(--muted); margin-top:3px; line-height:1.5; }

        .goc-block { background:#f0f7f4; border:1px solid #c8ddd3; border-radius:12px; padding:18px 20px; margin:20px 0; }
        .goc-block ul { margin:6px 0 12px; padding-left:18px; }
        .goc-recommendation { font-size:13px; line-height:1.7; color:var(--text); }

        .match-list { margin-top:20px; padding:14px 18px; background:#faf8f2; border:1px solid var(--line); border-radius:12px; }
        .match-list summary { cursor:pointer; font-weight:600; padding:6px 0; }
        .match-list[open] summary { margin-bottom:14px; }
        .match-table { font-size:11px; }
        .match-table td { padding:5px 8px; }

        .diagnostic { background:#fff8e6; border-left:4px solid var(--warn); padding:10px 14px; margin:16px 0; font-size:13px; line-height:1.6; }
        .warn-banner { background:#fde8e8; border:1px solid #f0d5d5; border-radius:12px; padding:14px 18px; color:var(--danger); }

        .footer { padding:18px 32px 24px; color:var(--muted); font-size:12px; line-height:1.7; }
        .muted { color:var(--muted); }

        @media (max-width:900px) { .grid { grid-template-columns:repeat(2,minmax(0,1fr)); } header { flex-direction:column; } }
    """

    today = dt.date.today().isoformat()
    title_parts = [
        _safe(patient.get("cancer_type", "?")),
        " ".join(_safe(m) for m in patient.get("mutations", [])),
    ]
    full_title = "临床试验匹配报告 — v1.6.0 — " + " | ".join(p for p in title_parts if p)

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{full_title}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
    <header>
        <div>
            <h1>临床试验决策报告</h1>
            <div class="hero-sub">Clinical Trial Decision Report — TrialGPT v1.6.0 (CancerDAO fork)</div>
        </div>
        <div class="meta">
            报告日期: {today}<br/>
            患者 ID: {_safe(patient.get('patient_id','?'))}<br/>
            覆盖检索: ClinicalTrials.gov + ChiCTR<br/>
            报告版本: v1.6.0 (双层结构: Decision + Match)
        </div>
    </header>

    <section class="section">
        <div class="title">患者画像</div>
        <p class="muted" style="margin:0 0 14px">{_safe(patient.get('summary',''))}</p>
        {pat_cards}
    </section>

    {consistency_html}

    {goc_html}

    <section class="section">
        <div class="title">Decision Report — Top {len(paths)} 路径</div>
        {diag_html}
        {paths_html}
    </section>

    {soc_html}

    <section class="section">
        {match_html}
    </section>

    <section class="section">
        <div class="title">声明 + 校验摘要</div>
        <p style="font-size:13px; color:var(--muted); line-height:1.7">
            <strong>声明:</strong> 本报告由 clinical-trial-matching {_safe(report.get('report_version','v2.0.0'))} 系统生成，仅提供临床试验<strong>信息匹配</strong>，
            不构成医疗建议或治疗推荐。所有入组资格需由临床研究团队最终审核确认。
            报告中"feasibility"和"vs SoC"是基于公开协议入排标准 + 公开发表数据的形式比对，
            不代表对治疗效果或风险的临床判断。
        </p>
        <div style="font-size:11px; color:var(--muted); border-top:1px solid var(--line); padding-top:10px">
            校验摘要: {sum(1 for t in gated_data.get('match',[])+gated_data.get('conditional',[]) if _verification(t).get('overall_status')=='RECRUITING')} 个 NCT ID 经 ClinicalTrials.gov v2 API 验证为 RECRUITING 状态（ChiCTR 试验在中国注册中心人工核验，不计入此摘要）。
            决策路径数: {len(paths)}; Match 池: {len(gated_data.get('match',[]))}; Conditional: {len(gated_data.get('conditional',[]))}; Exclude: {len(gated_data.get('exclude',[]))}.
            一致性标记: {len(consistency)} 项 (患者画像内在张力).
            治疗目标讨论: {'已触发' if goc.get('triggered') else '未触发'}.
        </div>
    </section>

    <div class="footer">
        生成于 {report.get('generated_at','?')} · 工具: clinical-trial-matching-skill {_safe(report.get('report_version','v2.0.0'))} (CancerDAO/TrialGPT fork)
    </div>
</div>
</body>
</html>"""

    Path(output_path).write_text(html_doc, encoding="utf-8")
    return output_path


# CLI
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="Decision report JSON")
    parser.add_argument("--gated", required=True, help="Gated/scored JSON (for match list)")
    parser.add_argument("--patient", required=True, help="Patient profile JSON")
    parser.add_argument("--out", required=True, help="Output HTML path")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)
    with open(args.gated) as f:
        gated = json.load(f)
    with open(args.patient) as f:
        patient = json.load(f)

    out = render_html(report, gated, patient, args.out)
    print(f"Rendered: {out}")
