import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
from datetime import date
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_journal, get_available_months
from core.metrics import calc_kpi, calc_health_score
from core.aging import calc_aging, calc_concentration

st.set_page_config(
    page_title="곡성안전유리 경영 현황",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 비밀번호 ──────────────────────────────────────────────────────────────
if not st.session_state.get("authenticated"):
    st.markdown("## 🏭 곡성안전유리 경영 현황")
    st.markdown("---")
    pw = st.text_input("접속 비밀번호", type="password", placeholder="비밀번호 입력")
    if st.button("로그인", type="primary", use_container_width=True):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

# ── 월 선택 ───────────────────────────────────────────────────────────────
months = get_available_months()
if not months:
    st.warning("저장된 데이터가 없습니다. **데이터 허브** 메뉴에서 분개장을 업로드하세요.")
    st.stop()

years = sorted(set(m[:4] for m in months), reverse=True)

col_yr, col_mo = st.columns([1, 5])
with col_yr:
    sel_year = st.selectbox("년도", years, index=0, label_visibility="collapsed")

with col_mo:
    avail = sorted([m for m in months if m.startswith(sel_year)])
    mo_labels = [f"{m[5:7]}월" for m in avail]
    sel_idx = st.radio(
        "월", range(len(avail)),
        format_func=lambda i: mo_labels[i],
        index=len(avail) - 1,
        horizontal=True,
        label_visibility="collapsed",
    )
    selected_ym = avail[sel_idx]

is_latest = selected_ym == months[0]
st.markdown(
    f"**현재 기준: {selected_ym[:4]}년 {selected_ym[5:7]}월**"
    + ("　`최신 데이터`" if is_latest else ""),
)

# ── 데이터 로드 ───────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data(ym: str):
    df = load_journal(ym)
    if df.empty:
        return df, {}
    if "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    kpi = calc_kpi(df)
    return df, kpi

with st.spinner("데이터 불러오는 중..."):
    df, kpi = load_data(selected_ym)

if df.empty:
    st.error(f"{selected_ym} 데이터가 없습니다.")
    st.stop()

# 전월 데이터
prev_kpi = None
prev_ym = None
idx = months.index(selected_ym)
if idx < len(months) - 1:
    prev_ym = months[idx + 1]
    _, prev_kpi = load_data(prev_ym)

# 건강점수 (이번달 AR 기준 — 홈 화면 빠른 표시용)
aging_df = calc_aging(df)
conc = calc_concentration(aging_df)
kpi["상위5집중도"] = conc["상위5집중도"]
if prev_kpi and prev_ym:
    prev_df, _ = load_data(prev_ym)  # 캐시됨, 재요청 비용 없음
    prev_aging = calc_aging(prev_df)
    prev_conc = calc_concentration(prev_aging)
    prev_kpi["상위5집중도"] = prev_conc["상위5집중도"]

health = calc_health_score(kpi, prev_kpi)

매출액 = kpi["매출액"]
영업이익_v7 = kpi["영업이익_v7"]
영업이익률_v7 = kpi["영업이익률_v7"]
이자비용 = kpi["이자비용"]
자산처분손실 = kpi["자산처분손실"]
영업외수익 = kpi["영업외수익"]
영업외수익_반복 = kpi["영업외수익_반복"]
영업외수익_일회성 = kpi["영업외수익_일회성"]
실질이익 = kpi["실질이익"]
원재료순 = kpi["원재료순"]
부재료매입 = kpi["부재료매입"]
인건비 = kpi["인건비"]
기타운영비 = kpi["운영비_기타"] - 인건비

# ── 헤더 ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("## 🏭 곡성안전유리 경영 현황")

# ── 건강점수 도넛 + 4대 신호등 ────────────────────────────────────────────
col_donut, col_risks = st.columns([1, 2])

with col_donut:
    if health["is_baseline"]:
        score_color = "#6b7280"
        fig_score = go.Figure(go.Pie(
            values=[100], labels=[""], hole=0.65,
            marker_colors=["#374151"], showlegend=False, textinfo="none",
        ))
        center_txt = "기준월"
    else:
        score = health["총점"]
        score_color = "#4ade80" if score >= 80 else "#fb923c" if score >= 60 else "#f87171"
        fig_score = go.Figure(go.Pie(
            values=[score, 100 - score], labels=["", ""], hole=0.65,
            marker_colors=[score_color, "#1f2937"],
            showlegend=False, textinfo="none",
        ))
        center_txt = f"{score}점"

    fig_score.update_layout(
        height=200, margin=dict(t=8, b=8, l=8, r=8),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(
            text=f"<b>{center_txt}</b><br><span style='font-size:11px;color:#9ca3af'>건강점수</span>",
            x=0.5, y=0.5, font_size=20, font_color=score_color, showarrow=False,
        )],
    )
    st.plotly_chart(fig_score, use_container_width=True)

with col_risks:
    risk_map = health["리스크"]
    icon_map = {"green": "🟢", "yellow": "🟡", "red": "🔴", "baseline": "⚪"}
    bg_map = {
        "green": "#14532d", "yellow": "#78350f",
        "red": "#7f1d1d", "baseline": "#1f2937",
    }
    ri_cols = st.columns(2)
    for i, (rname, info) in enumerate(risk_map.items()):
        icon = icon_map.get(info["상태"], "⚪")
        bg = bg_map.get(info["상태"], "#1f2937")
        score_txt = f"{info['점수']}/{info['만점']}점" if info.get("점수") is not None else ""
        ri_cols[i % 2].markdown(
            f"""<div style='background:{bg};border-radius:8px;padding:10px 12px;margin-bottom:8px;'>
            <span style='font-size:16px;'>{icon}</span>
            <b style='color:white;font-size:14px;'> {rname}</b>
            <span style='color:#9ca3af;font-size:12px;float:right;'>{score_txt}</span><br>
            <span style='color:#d1d5db;font-size:12px;'>{info['사유']}</span>
            </div>""",
            unsafe_allow_html=True,
        )

# ── 이번 달 손익 ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader(f"📊 {selected_ym[:4]}년 {selected_ym[5:7]}월 손익")

def pct_str(v):
    return f"{v / 매출액 * 100:.1f}%" if 매출액 > 0 else "—"

def fmt_억(v):
    return f"{v / 1e8:.2f}억원"

pl_rows = []
pl_rows.append({"항목": "매출액", "금액": fmt_억(매출액), "매출대비": "100%"})
pl_rows.append({"항목": "원재료매입 (*)", "금액": f"−{fmt_억(원재료순)}", "매출대비": pct_str(원재료순)})
pl_rows.append({"항목": "부재료매입", "금액": f"−{fmt_억(부재료매입)}", "매출대비": pct_str(부재료매입)})
pl_rows.append({"항목": "인건비", "금액": f"−{fmt_억(인건비)}", "매출대비": pct_str(인건비)})
pl_rows.append({"항목": "기타운영비", "금액": f"−{fmt_억(기타운영비)}", "매출대비": pct_str(기타운영비)})

margin_diff_str = ""
if prev_kpi:
    diff = 영업이익률_v7 - prev_kpi.get("영업이익률_v7", prev_kpi["영업이익률"])
    margin_diff_str = f"전월비 {diff:+.1f}%p"
pl_rows.append({"항목": "── 영업이익 ──", "금액": fmt_억(영업이익_v7), "매출대비": pct_str(영업이익_v7) + (f"  ({margin_diff_str})" if margin_diff_str else "")})
if 영업외수익_반복 > 0:
    pl_rows.append({"항목": "영업외수익 (이자수익)", "금액": f"+{fmt_억(영업외수익_반복)}", "매출대비": pct_str(영업외수익_반복)})
if 영업외수익_일회성 > 0:
    pl_rows.append({"항목": "영업외수익 (일회성)", "금액": f"+{fmt_억(영업외수익_일회성)}", "매출대비": pct_str(영업외수익_일회성)})
pl_rows.append({"항목": "이자비용", "금액": f"−{fmt_억(이자비용)}", "매출대비": pct_str(이자비용)})
if 자산처분손실 > 0:
    pl_rows.append({"항목": "일회성손익 (감가상각·처분)", "금액": f"−{fmt_억(자산처분손실)}", "매출대비": pct_str(자산처분손실)})
pl_rows.append({"항목": "── 실질이익 ──", "금액": fmt_억(실질이익), "매출대비": pct_str(실질이익)})

st.dataframe(pd.DataFrame(pl_rows), use_container_width=True, hide_index=True, height=315)
st.caption("(*) 원재료매입은 이달 매입 기준 — 재고 반영 전. 연말 회계 대체(Code 455)는 제외.")

# ── 비용 구조 도넛 + 항목별 금액 ─────────────────────────────────────────
st.markdown("---")
col_pie, col_bar = st.columns([1, 1])

cost_display = dict(kpi["비용대분류_v7"])
if 원재료순 > 0:
    cost_display["원재료매입"] = 원재료순
if 부재료매입 > 0:
    cost_display["부재료매입"] = 부재료매입
cost_display = {k: v for k, v in cost_display.items() if v > 0}

PALETTE = ["#60a5fa", "#f87171", "#34d399", "#fbbf24", "#a78bfa",
           "#fb923c", "#38bdf8", "#4ade80", "#f472b6", "#94a3b8", "#e879f9", "#facc15"]

with col_pie:
    st.subheader("비용 구조")
    if cost_display:
        labels = list(cost_display.keys())
        values = list(cost_display.values())
        fig_pie = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.4,
            marker_colors=PALETTE[:len(labels)],
            textinfo="label+percent", textfont_size=11,
        ))
        fig_pie.update_layout(
            height=300, margin=dict(t=10, b=10, l=0, r=0),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"), showlegend=False,
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("이달 비용 데이터 없음")

with col_bar:
    st.subheader("항목별 금액")
    if cost_display and 매출액 > 0:
        sorted_costs = sorted(cost_display.items(), key=lambda x: -x[1])
        for name, val in sorted_costs[:9]:
            pv = val / 매출액 * 100
            bar_w = min(int(pv * 2.5), 100)
            st.markdown(
                f"""<div style='margin-bottom:5px;'>
                <span style='font-size:13px;color:#e5e7eb;'>{name}</span>
                <span style='float:right;font-size:12px;color:#9ca3af;'>{val/1e6:.0f}백만 ({pv:.1f}%)</span>
                <div style='background:#374151;border-radius:3px;height:5px;margin-top:3px;'>
                <div style='background:#60a5fa;width:{bar_w}%;height:5px;border-radius:3px;'></div>
                </div></div>""",
                unsafe_allow_html=True,
            )

# ── 매출채권 현황 (이번달 기준 간략 표시) ─────────────────────────────────
st.markdown("---")
col_ar, col_rev = st.columns([1, 1])

with col_ar:
    st.subheader("📋 매출채권 현황")
    if not aging_df.empty:
        def risk_badge(row):
            if row["악성(91+)"] > 0: return "🔴 위험"
            if row["경고(61-90)"] > 0: return "🟠 경고"
            if row["주의(31-60)"] > 0: return "🟡 주의"
            return "🟢 정상"

        top_ar = aging_df.head(8).copy()
        top_ar["위험도"] = top_ar.apply(risk_badge, axis=1)
        top_ar["잔액"] = top_ar["잔액"].apply(lambda v: f"{v/1e6:.1f}백만")
        st.dataframe(
            top_ar[["거래처", "잔액", "위험도"]],
            use_container_width=True, hide_index=True, height=300,
        )
        st.caption(f"총 미수금: {conc['총잔액']/1e6:.0f}백만원 | 누적 정밀 분석 → 매출채권관리 페이지")
    else:
        st.info("이달 매출채권 데이터 없음")

with col_rev:
    st.subheader("📈 거래처별 매출")
    rev_by_partner = (
        df[df["계정코드"].astype(str) == "404"]
        .groupby("거래처")["대변"]
        .sum()
        .sort_values(ascending=False)
        .head(8)
    )
    if not rev_by_partner.empty:
        top1_pct = rev_by_partner.iloc[0] / 매출액 * 100 if 매출액 > 0 else 0
        if top1_pct >= 30:
            st.warning(f"⚠️ {rev_by_partner.index[0]}: {top1_pct:.1f}% 집중")
        fig_bar = go.Figure(go.Bar(
            x=rev_by_partner.values / 1e6,
            y=rev_by_partner.index,
            orientation="h",
            marker_color="#60a5fa",
            text=[f"{v/1e6:.0f}백만" for v in rev_by_partner.values],
            textposition="outside",
        ))
        fig_bar.update_layout(
            height=300, margin=dict(t=5, b=5, l=5, r=50),
            xaxis_title="백만원",
            yaxis=dict(autorange="reversed"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("매출 거래처 데이터 없음")

# ── 다음 달 챙길 일 ───────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 다음 달 챙길 일")

actions = []
if 영업이익_v7 < 0:
    actions.append(f"🚨 **영업 적자 {abs(영업이익_v7)/1e6:.0f}백만원** — 비용구조 긴급 점검 (원가비용통제 페이지)")
elif prev_kpi:
    diff = 영업이익률_v7 - prev_kpi.get("영업이익률_v7", prev_kpi["영업이익률"])
    if diff < -2:
        actions.append(f"⚠️ 영업이익률 전월비 {diff:+.1f}%p 하락 — 원인 파악 필요")
if not aging_df.empty and aging_df["악성(91+)"].sum() > 0:
    악성금액 = aging_df["악성(91+)"].sum()
    악성업체 = aging_df[aging_df["악성(91+)"] > 0].iloc[0]["거래처"]
    actions.append(f"🚨 악성 미수금 **{악성금액/1e6:.0f}백만원** ({악성업체} 등) — 즉시 회수 연락")
if conc["상위5집중도"] >= 50:
    actions.append(f"⚠️ 매출채권 집중도 {conc['상위5집중도']:.1f}% — 거래처 분산 검토")
if 이자비용 > 0:
    actions.append(f"💰 이자비용 {이자비용/1e4:.0f}만원/월 — 부채이자관리 페이지 확인")
if not actions:
    actions.append("✅ 특별한 긴급 항목 없음 — 정기 모니터링 유지")
for a in actions:
    st.markdown(f"- {a}")

# ── 경영 데이터 내보내기 ──────────────────────────────────────────────────
st.markdown("---")
st.subheader("📤 경영 데이터 내보내기")
st.caption("JSON을 다운로드 → Claude.ai에 업로드해서 자유롭게 경영 조언을 받으세요.")

export_data = {
    "기준월": selected_ym,
    "생성일": date.today().isoformat(),
    "손익": {
        "매출액_억": round(매출액 / 1e8, 2),
        "원재료매입_억": round(원재료순 / 1e8, 2),
        "부재료매입_억": round(부재료매입 / 1e8, 2),
        "인건비_억": round(인건비 / 1e8, 2),
        "기타운영비_억": round(기타운영비 / 1e8, 2),
        "영업이익_억": round(영업이익_v7 / 1e8, 2),
        "영업이익률": f"{영업이익률_v7:.1f}%",
        "이자비용_억": round(이자비용 / 1e8, 2),
        "실질이익_억": round(실질이익 / 1e8, 2),
    },
    "비용대분류": {
        k: round(v / 1e6, 1) for k, v in cost_display.items()
    },
    "건강점수": {
        "총점": health["총점"],
        "is_baseline": health["is_baseline"],
        "리스크": {
            k: {"점수": v["점수"], "만점": v["만점"], "사유": v["사유"]}
            for k, v in health["리스크"].items()
        },
    },
    "매출채권": {
        "총잔액_억": round(conc["총잔액"] / 1e8, 2),
        "상위5집중도": f"{conc['상위5집중도']:.1f}%",
        "거래처별_백만": [
            {
                "거래처": r["거래처"],
                "잔액_백만": round(r["잔액"] / 1e6, 1),
                "악성91일이상_백만": round(r.get("악성(91+)", 0) / 1e6, 1),
            }
            for r in aging_df.head(10).to_dict("records")
        ] if not aging_df.empty else [],
    },
}

st.download_button(
    label="📥 경영 데이터 JSON 다운로드",
    data=json.dumps(export_data, ensure_ascii=False, indent=2),
    file_name=f"경영데이터_{selected_ym}.json",
    mime="application/json",
    type="primary",
    use_container_width=True,
)
