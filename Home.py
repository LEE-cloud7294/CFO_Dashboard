import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import calendar
from datetime import date
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_journal, load_journal_upto, get_available_months, get_annual_dep
from core.metrics import calc_kpi, calc_health_score, apply_monthly_depreciation, fmt_krw
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

# ── 기준일 계산 ───────────────────────────────────────────────────────────
_yr, _mo = int(selected_ym[:4]), int(selected_ym[5:7])
_last_day = calendar.monthrange(_yr, _mo)[1]
as_of_date = date(_yr, _mo, _last_day)

# ── 데이터 로드 함수 ──────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data(ym: str):
    df = load_journal(ym)
    if df.empty:
        return df, {}
    if "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    kpi = calc_kpi(df)
    return df, kpi


@st.cache_data(ttl=3600)
def load_ar_cumulative(ym: str) -> pd.DataFrame:
    """FIFO 정확도를 위한 전체 누적 AR — 창업일~선택월 전체."""
    df = load_journal_upto(ym)
    if not df.empty and "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return df


@st.cache_data(ttl=3600)
def get_dep_cached(year: str) -> float:
    return get_annual_dep(year)


@st.cache_data(ttl=600)
def load_recent_trend(month_list: list) -> pd.DataFrame:
    """최근 N개월 KPI 추이 (최대 6개월)."""
    rows = []
    for ym in sorted(month_list[:6]):  # 오래된 순
        df_m = load_journal(ym)
        if df_m.empty:
            continue
        if "계정그룹" not in df_m.columns:
            df_m["계정그룹"] = df_m["계정코드"].astype(str).str[:1]
        k = calc_kpi(df_m)
        annual_dep = get_dep_cached(ym[:4])
        if annual_dep > 0:
            k = apply_monthly_depreciation(k, annual_dep / 12)
        비용분류 = k.get("비용대분류_v7", {})
        매출_t = k.get("매출액", 0)
        총인건비_t = (k.get("인건비", 0)
                    + 비용분류.get("퇴직급여", 0)
                    + 비용분류.get("4대보험", 0)
                    + 비용분류.get("복리후생", 0))
        원재료_t = k.get("원재료순", k.get("원재료", 0))
        부재료_t = k.get("부재료매입", k.get("부재료", 0))
        rows.append({
            "ym": ym,
            "label": f"{ym[2:4]}.{ym[5:7]}",
            "매출액": 매출_t,
            "영업이익률": k.get("영업이익률_v7", k.get("영업이익률", 0)),
            "총인건비율": (총인건비_t / 매출_t * 100) if 매출_t > 0 else 0,
            "원부재료율": ((원재료_t + 부재료_t) / 매출_t * 100) if 매출_t > 0 else 0,
        })
    return pd.DataFrame(rows)


# ── 이달 데이터 로드 ──────────────────────────────────────────────────────
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

# ── 매출채권 AR — 전체 누적 로드 (정확도 우선) ────────────────────────────
with st.spinner("매출채권 누적 데이터 분석 중..."):
    ar_df = load_ar_cumulative(selected_ym)

aging_df = calc_aging(ar_df, as_of=as_of_date) if not ar_df.empty else calc_aging(df)
conc = calc_concentration(aging_df)
kpi["상위5집중도"] = conc["상위5집중도"]

if prev_kpi and prev_ym:
    prev_yr, prev_mo = int(prev_ym[:4]), int(prev_ym[5:7])
    prev_last = calendar.monthrange(prev_yr, prev_mo)[1]
    prev_as_of = date(prev_yr, prev_mo, prev_last)
    prev_ar_df = load_ar_cumulative(prev_ym)
    prev_aging = calc_aging(prev_ar_df, as_of=prev_as_of) if not prev_ar_df.empty else calc_aging(load_data(prev_ym)[0])
    prev_conc = calc_concentration(prev_aging)
    prev_kpi["상위5집중도"] = prev_conc["상위5집중도"]

# ── 감가상각 월배분 ──────────────────────────────────────────────────────
annual_dep = get_dep_cached(selected_ym[:4])
if annual_dep > 0:
    monthly_dep = annual_dep / 12
    kpi = apply_monthly_depreciation(kpi, monthly_dep)
    if prev_kpi:
        prev_annual_dep = get_dep_cached(prev_ym[:4] if prev_ym else selected_ym[:4])
        if prev_annual_dep > 0:
            prev_kpi = apply_monthly_depreciation(prev_kpi, prev_annual_dep / 12)

# ── 인건비성 비용 세분화 ──────────────────────────────────────────────────
비용분류_v7 = kpi.get("비용대분류_v7", {})
인건비 = kpi.get("인건비", 0)                         # 실급여만
퇴직급여 = 비용분류_v7.get("퇴직급여", 0)
사대보험 = 비용분류_v7.get("4대보험", 0)
복리후생 = 비용분류_v7.get("복리후생", 0)
퇴직보험복리 = 퇴직급여 + 사대보험 + 복리후생
총인건비 = 인건비 + 퇴직보험복리
대손상각 = 비용분류_v7.get("대손상각", 0)

# 기타운영비 = 인건비성 모두 제외한 나머지
기타운영비 = kpi.get("운영비_기타", kpi.get("총비용", 0)) - 총인건비

# ── 이달 손익 지표 ────────────────────────────────────────────────────────
매출액 = kpi.get("매출액", 0)
영업이익_v7 = kpi.get("영업이익_v7", kpi.get("영업이익", 0))
영업이익률_v7 = kpi.get("영업이익률_v7", kpi.get("영업이익률", 0))
이자비용 = kpi.get("이자비용", 0)
자산처분손실 = kpi.get("자산처분손실", 0)
영업외수익_반복 = kpi.get("영업외수익_반복", 0)
영업외수익_일회성 = kpi.get("영업외수익_일회성", 0)
실질이익 = kpi.get("실질이익", 영업이익_v7 - 이자비용)
원재료순 = kpi.get("원재료순", kpi.get("원재료", 0))
부재료매입 = kpi.get("부재료매입", kpi.get("부재료", 0))

# ── 자금경색 자동 계산 ────────────────────────────────────────────────────
# 이달 보통예금(103) 입금 총액 = 현금 유입 근사치
try:
    cash_103 = df[df["계정코드"].astype(str) == "103"]
    cash_data = {
        "월중입금": float(cash_103["차변"].sum()),
        "예상고정비": float(총인건비 + 이자비용),
    }
except Exception:
    cash_data = None

# ── 건강점수 계산 ─────────────────────────────────────────────────────────
try:
    health = calc_health_score(kpi, prev_kpi, cash_data=cash_data)
except TypeError:
    # cash_data 파라미터 미지원 구버전 호환
    health = calc_health_score(kpi, prev_kpi)

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

pl_rows = []
pl_rows.append({"항목": "매출액", "금액": fmt_krw(매출액), "매출대비": "100%"})
pl_rows.append({"항목": "원재료매입 (*)", "금액": f"−{fmt_krw(원재료순)}", "매출대비": pct_str(원재료순)})
pl_rows.append({"항목": "부재료매입", "금액": f"−{fmt_krw(부재료매입)}", "매출대비": pct_str(부재료매입)})
pl_rows.append({"항목": "인건비 (실급여)", "금액": f"−{fmt_krw(인건비)}", "매출대비": pct_str(인건비)})
if 퇴직보험복리 > 0:
    pl_rows.append({"항목": "퇴직·보험·복리", "금액": f"−{fmt_krw(퇴직보험복리)}", "매출대비": pct_str(퇴직보험복리)})
pl_rows.append({"항목": "기타운영비", "금액": f"−{fmt_krw(기타운영비)}", "매출대비": pct_str(기타운영비)})

margin_diff_str = ""
if prev_kpi:
    diff = 영업이익률_v7 - prev_kpi.get("영업이익률_v7", prev_kpi.get("영업이익률", 0))
    margin_diff_str = f"전월비 {diff:+.1f}%p"
dep_월 = kpi.get("감가상각_월", 0)
if dep_월 > 0:
    pl_rows.append({"항목": "감가상각비 (월배분)", "금액": f"−{fmt_krw(dep_월)}", "매출대비": pct_str(dep_월)})
pl_rows.append({"항목": "── 영업이익 ──", "금액": fmt_krw(영업이익_v7),
                "매출대비": pct_str(영업이익_v7) + (f"  ({margin_diff_str})" if margin_diff_str else "")})
if 영업외수익_반복 > 0:
    pl_rows.append({"항목": "영업외수익 (이자수익)", "금액": f"+{fmt_krw(영업외수익_반복)}", "매출대비": pct_str(영업외수익_반복)})
if 영업외수익_일회성 > 0:
    pl_rows.append({"항목": "영업외수익 (일회성)", "금액": f"+{fmt_krw(영업외수익_일회성)}", "매출대비": pct_str(영업외수익_일회성)})
pl_rows.append({"항목": "이자비용", "금액": f"−{fmt_krw(이자비용)}", "매출대비": pct_str(이자비용)})
if 자산처분손실 > 0:
    pl_rows.append({"항목": "일회성손익 (처분)", "금액": f"−{fmt_krw(자산처분손실)}", "매출대비": pct_str(자산처분손실)})
pl_rows.append({"항목": "── 실질이익 ──", "금액": fmt_krw(실질이익), "매출대비": pct_str(실질이익)})

st.dataframe(pd.DataFrame(pl_rows), use_container_width=True, hide_index=True, height=350)
st.caption("(*) 원재료매입은 이달 매입 기준. 연말 회계 대체(Code 455) 제외.")

# ── 최근 6개월 추이 ──────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📈 최근 6개월 추이")

trend_df = load_recent_trend(months)
if not trend_df.empty and len(trend_df) >= 2:
    fig_trend = go.Figure()
    # 매출액 바차트 (왼쪽 Y)
    fig_trend.add_trace(go.Bar(
        name="매출액",
        x=trend_df["label"],
        y=trend_df["매출액"] / 1e6,
        marker_color="#60a5fa",
        opacity=0.8,
        text=(trend_df["매출액"] / 1e6).apply(lambda v: f"{v:.0f}"),
        textposition="outside",
        yaxis="y",
    ))
    # 영업이익률 꺾은선 (오른쪽 Y)
    line_color = "#34d399"
    fig_trend.add_trace(go.Scatter(
        name="영업이익률",
        x=trend_df["label"],
        y=trend_df["영업이익률"],
        mode="lines+markers+text",
        line=dict(color=line_color, width=2),
        marker=dict(size=7),
        text=trend_df["영업이익률"].apply(lambda v: f"{v:.1f}%"),
        textposition="top center",
        textfont=dict(size=10, color=line_color),
        yaxis="y2",
    ))
    # 총인건비율 꺾은선 (오른쪽 Y)
    fig_trend.add_trace(go.Scatter(
        name="총인건비율",
        x=trend_df["label"],
        y=trend_df["총인건비율"],
        mode="lines+markers",
        line=dict(color="#f87171", width=2, dash="dot"),
        marker=dict(size=6),
        yaxis="y2",
    ))
    # 원재료+부재료율 꺾은선 (오른쪽 Y)
    fig_trend.add_trace(go.Scatter(
        name="원+부재료율",
        x=trend_df["label"],
        y=trend_df["원부재료율"],
        mode="lines+markers+text",
        line=dict(color="#fbbf24", width=2),
        marker=dict(size=7),
        text=trend_df["원부재료율"].apply(lambda v: f"{v:.1f}%"),
        textposition="bottom center",
        textfont=dict(size=10, color="#fbbf24"),
        yaxis="y2",
    ))
    y2_max = max(
        trend_df[["영업이익률", "총인건비율", "원부재료율"]].max().max() + 8,
        60
    )
    fig_trend.update_layout(
        yaxis=dict(title="매출액 (백만원)", tickformat=",.0f"),
        yaxis2=dict(title="%", overlaying="y", side="right",
                    ticksuffix="%", range=[-25, y2_max]),
        height=340,
        margin=dict(t=20, b=30),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        barmode="group",
    )
    st.plotly_chart(fig_trend, use_container_width=True)
    st.caption("파란 막대=매출액(백만원) | 초록=영업이익률 | 빨간점=총인건비율 | 노란=원+부재료율")
else:
    st.info("최소 2개월 데이터 필요")

# ── 비용 구조 도넛 + 항목별 금액 ─────────────────────────────────────────
st.markdown("---")
col_pie, col_bar = st.columns([1, 1])

cost_display = dict(비용분류_v7)
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

# ── 매출채권 현황 (누적 기준) + 거래처별 매출 ─────────────────────────────
st.markdown("---")
col_ar, col_rev = st.columns([1, 1])

with col_ar:
    st.subheader("📋 매출채권 현황")
    st.caption("창업일~선택월 전체 누적 기준 FIFO")
    if not aging_df.empty:
        def risk_badge(row):
            if row["악성(91+)"] > 0: return "🔴 위험"
            if row["경고(61-90)"] > 0: return "🟠 경고"
            if row["주의(31-60)"] > 0: return "🟡 주의"
            return "🟢 정상"

        top_ar = aging_df.sort_values("잔액", ascending=False).head(8).copy()
        top_ar["위험도"] = top_ar.apply(risk_badge, axis=1)
        top_ar["잔액"] = top_ar["잔액"].apply(lambda v: f"{v/1e6:.1f}백만")
        st.dataframe(
            top_ar[["거래처", "잔액", "위험도"]],
            use_container_width=True, hide_index=True, height=300,
        )
        st.caption(f"총 미수금: {conc['총잔액']/1e6:.0f}백만원 | 정밀 분석 → 매출채권관리 페이지")
    else:
        st.info("매출채권 데이터 없음")

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

# 영업 적자
if 영업이익_v7 < 0:
    actions.append(f"🚨 **영업 적자 {abs(영업이익_v7)/1e6:.0f}백만원** — 비용구조 긴급 점검 (손익분석 페이지)")
elif prev_kpi:
    diff = 영업이익률_v7 - prev_kpi.get("영업이익률_v7", prev_kpi.get("영업이익률", 0))
    if diff < -2:
        actions.append(f"⚠️ 영업이익률 전월비 {diff:+.1f}%p 하락 — 원인 파악 필요")

# 총인건비율 경보 (35% 초과 시)
if 매출액 > 0:
    총인건비율 = 총인건비 / 매출액 * 100
    if 총인건비율 >= 38:
        actions.append(f"🚨 총 인건비성 비용 **{총인건비율:.1f}%** — 매출 대비 위험 수준 (인건비+퇴직+4대보험)")
    elif 총인건비율 >= 33:
        actions.append(f"⚠️ 총 인건비성 비용 {총인건비율:.1f}% — 주의 수준 ({fmt_krw(총인건비)})")

# 대손상각 발생
if 대손상각 > 0:
    actions.append(f"⚠️ 이달 대손상각비 **{fmt_krw(대손상각)}** 발생 — 해당 거래처 미수금 대조 필요")

# 악성 미수금
if not aging_df.empty and aging_df["악성(91+)"].sum() > 0:
    악성금액 = aging_df["악성(91+)"].sum()
    악성업체 = aging_df[aging_df["악성(91+)"] > 0].iloc[0]["거래처"]
    actions.append(f"🚨 악성 미수금 **{악성금액/1e6:.0f}백만원** ({악성업체} 등) — 즉시 회수 연락")

# 집중도
if conc["상위5집중도"] >= 50:
    actions.append(f"⚠️ 매출채권 집중도 {conc['상위5집중도']:.1f}% — 거래처 분산 검토")

# 이자비용
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
        "인건비_실급여_억": round(인건비 / 1e8, 2),
        "퇴직보험복리_억": round(퇴직보험복리 / 1e8, 2),
        "총인건비_억": round(총인건비 / 1e8, 2),
        "기타운영비_억": round(기타운영비 / 1e8, 2),
        "영업이익_억": round(영업이익_v7 / 1e8, 2),
        "영업이익률": f"{영업이익률_v7:.1f}%",
        "총인건비율": f"{(총인건비/매출액*100) if 매출액 > 0 else 0:.1f}%",
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
            for r in aging_df.sort_values("잔액", ascending=False).head(10).to_dict("records")
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
