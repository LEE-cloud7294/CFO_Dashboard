import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months, get_annual_dep
from core.metrics import calc_kpi, apply_monthly_depreciation, fmt_krw

st.set_page_config(page_title="연간 비교", page_icon="📅", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("📅 연간 경영지표 비교")
st.caption("연도별 손익 추세 | 감가상각 ÷12 균등 반영 | 원재료는 실제 매입 기준")

months = get_available_months()
if not months:
    st.warning("데이터가 없습니다. 데이터 허브에서 분개장을 업로드해 주세요.")
    st.stop()

all_years = sorted(set(m[:4] for m in months))


@st.cache_data(ttl=600)
def load_annual_kpi(year: str, month_list: list[str]) -> dict:
    """연간 KPI 합산 (감가상각 ÷12 연간 총액 반영)."""
    year_months = [m for m in month_list if m.startswith(year)]
    if not year_months:
        return {}

    all_dfs = []
    for ym in year_months:
        df = load_journal(ym)
        if not df.empty:
            if "계정그룹" not in df.columns:
                df["계정그룹"] = df["계정코드"].astype(str).str[:1]
            all_dfs.append(df)

    if not all_dfs:
        return {}

    combined = pd.concat(all_dfs, ignore_index=True)
    kpi = calc_kpi(combined)

    # 감가상각 연간 총액 적용 (손익계산서와 동일 기준)
    annual_dep = get_annual_dep(year)
    if annual_dep > 0:
        kpi = apply_monthly_depreciation(kpi, annual_dep)  # 연간 총액 전달

    kpi["적재월수"] = len(year_months)
    return kpi


# ── 연도 선택 ─────────────────────────────────────────────────────────────────
sel_years = st.multiselect(
    "비교할 연도 선택 (최대 4개)",
    options=all_years,
    default=all_years[-3:] if len(all_years) >= 3 else all_years,
    max_selections=4,
)

if not sel_years:
    st.info("연도를 선택해 주세요.")
    st.stop()

with st.spinner("연간 데이터 집계 중..."):
    annual = {}
    for yr in sel_years:
        kpi = load_annual_kpi(yr, months)
        if kpi:
            annual[yr] = kpi

if not annual:
    st.error("선택한 연도의 데이터가 없습니다.")
    st.stop()

if "2022" in annual and annual["2022"].get("적재월수", 12) < 12:
    st.info(f"ℹ️ 2022년은 {annual['2022']['적재월수']}개월 데이터 (4월~12월)")

# ── 손익 비교표 ──────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📊 손익 비교표")
st.caption("금액: 원 단위 / % = 매출액 대비 / 증감 = 전년 대비 변화율")

# 행 정의: (표시명, kpi_key or None, 소계여부)
ROWS = [
    ("매출액",        "매출액",        False),
    ("원재료매입",    "원재료순",      False),
    ("부재료매입",    "부재료매입",    False),
    ("인건비",        "인건비",        False),
    ("전력·수도",     None,            False),
    ("물류·차량",     None,            False),
    ("유지·소모품",   None,            False),
    ("보험료",        None,            False),
    ("세금·임차",     None,            False),
    ("수수료",        None,            False),
    ("소모공구비",    None,            False),
    ("안전관리비",    None,            False),
    ("환경",          None,            False),
    ("판관비경비",    None,            False),
    ("연구개발",      None,            False),
    ("기타(미분류)",  None,            False),
    ("감가상각비",    "감가상각비",    False),
    ("영업이익",      "영업이익_v7",   True),
    ("이자비용",      "이자비용",      False),
    ("실질이익",      "실질이익",      True),
]

BUCKET_MAP = {
    "전력·수도":   "전력·수도",
    "물류·차량":   "물류·차량",
    "유지·소모품": "유지·소모품",
    "보험료":      "보험료",
    "세금·임차":   "세금·임차",
    "수수료":      "수수료",
    "소모공구비":  "하드웨어",
    "안전관리비":  "안전관리비",
    "환경":        "환경",
    "판관비경비":  "판관비경비",
    "연구개발":    "연구개발",
}

KNOWN_BUCKETS = set(BUCKET_MAP.values()) | {"인건비", "일회성손익", "원재료", "부재료"}


def get_val(kpi: dict, label: str, key) -> float:
    if key is not None:
        return kpi.get(key, 0)
    bucket = kpi.get("비용대분류_v7", {})
    if label in BUCKET_MAP:
        return bucket.get(BUCKET_MAP[label], 0)
    if label == "기타(미분류)":
        return sum(v for k, v in bucket.items() if k not in KNOWN_BUCKETS)
    return 0


yr_sorted = sorted(annual.keys())

# 표 생성
display_rows = []
for label, key, is_subtotal in ROWS:
    row = {"항목": "  " + label if not is_subtotal else f"▶ {label}"}
    prev_val = None
    prev_yr = None
    for yr in yr_sorted:
        kpi = annual[yr]
        val = get_val(kpi, label, key)
        매출 = kpi.get("매출액", 1) or 1
        pct = val / 매출 * 100

        # 금액 + % 표시
        row[f"{yr}년"] = f"{fmt_krw(val)}\n({pct:.1f}%)"

        # 전년 대비 증감
        if prev_val is not None and prev_yr is not None:
            if prev_val == 0:
                chg_str = "—"
            elif is_subtotal:
                # 이익률 %p 변화
                prev_pct = get_val(annual[prev_yr], label, key) / (annual[prev_yr].get("매출액", 1) or 1) * 100
                diff_pp = pct - prev_pct
                chg_str = f"{diff_pp:+.1f}%p"
            else:
                chg = (val - prev_val) / abs(prev_val) * 100
                chg_str = f"{chg:+.1f}%"
            row[f"→{yr}"] = chg_str

        prev_val = val
        prev_yr = yr

    display_rows.append(row)

disp_df = pd.DataFrame(display_rows)

# 컬럼 순서: 항목 → 연도1 → 증감 → 연도2 → 증감 → ...
ordered_cols = ["항목"]
for i, yr in enumerate(yr_sorted):
    ordered_cols.append(f"{yr}년")
    if i > 0:
        ordered_cols.insert(-1, f"→{yr}")

# 존재하는 컬럼만
ordered_cols = [c for c in ordered_cols if c in disp_df.columns]

# 증감 → 연도 순서 교정
final_cols = ["항목"]
for i, yr in enumerate(yr_sorted):
    if i > 0:
        final_cols.append(f"→{yr}")
    final_cols.append(f"{yr}년")

final_cols = [c for c in final_cols if c in disp_df.columns]
st.dataframe(disp_df[final_cols], use_container_width=True, hide_index=True)

# ── 매출액 추이 ───────────────────────────────────────────────────────────────
st.markdown("---")
col_bar1, col_bar2 = st.columns(2)

with col_bar1:
    st.subheader("매출액 추이")
    fig = go.Figure(go.Bar(
        x=[f"{y}년" for y in yr_sorted],
        y=[annual[y]["매출액"] for y in yr_sorted],
        marker_color="#60a5fa",
        text=[fmt_krw(annual[y]["매출액"]) for y in yr_sorted],
        textposition="outside",
    ))
    fig.update_layout(
        height=280, margin=dict(t=20, b=10),
        yaxis_title="원",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

with col_bar2:
    st.subheader("영업이익률 추이")
    rates = [annual[y].get("영업이익률_v7", 0) for y in yr_sorted]
    colors = ["#4ade80" if r >= 0 else "#f87171" for r in rates]
    fig2 = go.Figure(go.Bar(
        x=[f"{y}년" for y in yr_sorted],
        y=rates,
        marker_color=colors,
        text=[f"{r:.1f}%" for r in rates],
        textposition="outside",
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="#6b7280")
    fig2.update_layout(
        height=280, margin=dict(t=20, b=10),
        yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"), showlegend=False,
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── 비용 구조 추이 ────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("비용 구조 비교 (매출대비 %)")

COST_ITEMS = [
    ("원재료매입", "원재료순"),
    ("부재료매입", "부재료매입"),
    ("인건비",     "인건비"),
    ("전력·수도",  None),
    ("물류·차량",  None),
    ("감가상각비", "감가상각비"),
    ("기타",       None),
]
COLORS = ["#fbbf24","#fb923c","#f87171","#60a5fa","#34d399","#a78bfa","#94a3b8"]

fig3 = go.Figure()
for (label, key), color in zip(COST_ITEMS, COLORS):
    pcts = []
    for yr in yr_sorted:
        kpi = annual[yr]
        val = get_val(kpi, label, key) if key is None else kpi.get(key, 0)
        매출 = kpi.get("매출액", 1) or 1
        pcts.append(val / 매출 * 100)

    fig3.add_trace(go.Bar(
        name=label,
        x=[f"{y}년" for y in yr_sorted],
        y=pcts,
        marker_color=color,
        text=[f"{p:.1f}%" for p in pcts],
        textposition="inside", textfont_size=10,
    ))

fig3.update_layout(
    barmode="stack", height=350, margin=dict(t=20, b=10),
    yaxis_title="매출대비 %",
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig3, use_container_width=True)

# ── 인건비율·원재료율 추세 ─────────────────────────────────────────────────────
st.markdown("---")
col_l1, col_l2 = st.columns(2)

with col_l1:
    st.subheader("인건비율 추세")
    labor_pcts = [annual[y].get("인건비", 0) / (annual[y].get("매출액", 1) or 1) * 100 for y in yr_sorted]
    fig4 = go.Figure(go.Scatter(
        x=[f"{y}년" for y in yr_sorted], y=labor_pcts,
        mode="lines+markers+text",
        line=dict(color="#f87171", width=2), marker=dict(size=8),
        text=[f"{p:.1f}%" for p in labor_pcts], textposition="top center",
    ))
    fig4.update_layout(
        height=240, margin=dict(t=20, b=10), yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig4, use_container_width=True)

with col_l2:
    st.subheader("원재료매입율 추세")
    mat_pcts = [annual[y].get("원재료순", 0) / (annual[y].get("매출액", 1) or 1) * 100 for y in yr_sorted]
    fig5 = go.Figure(go.Scatter(
        x=[f"{y}년" for y in yr_sorted], y=mat_pcts,
        mode="lines+markers+text",
        line=dict(color="#fbbf24", width=2), marker=dict(size=8),
        text=[f"{p:.1f}%" for p in mat_pcts], textposition="top center",
    ))
    fig5.update_layout(
        height=240, margin=dict(t=20, b=10), yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig5, use_container_width=True)

# ── 방향 근거 ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 추세로 본 다음 해 방향")

if len(yr_sorted) >= 2:
    latest = annual[yr_sorted[-1]]
    prev = annual[yr_sorted[-2]]
    actions = []

    margin_cur = latest.get("영업이익률_v7", 0)
    margin_prv = prev.get("영업이익률_v7", 0)
    sales_chg = (latest.get("매출액", 0) - prev.get("매출액", 0)) / (prev.get("매출액", 1) or 1) * 100

    if sales_chg < -5:
        actions.append(f"📉 매출 {sales_chg:+.1f}% — 신규 거래처 발굴 및 이탈 방지")
    if margin_cur - margin_prv < -2:
        actions.append(f"⚠️ 영업이익률 {margin_cur - margin_prv:+.1f}%p — 원가 절감 또는 판가 인상 검토")
    if latest.get("영업이익_v7", 0) < 0:
        actions.append("🚨 영업 적자 구조 — 고정비 전면 재검토 필요")
    if not actions:
        actions.append("✅ 추세 양호 — 현 구조 유지하면서 성장 기회 모색")

    for a in actions:
        st.markdown(f"- {a}")
else:
    st.info("2개 연도 이상 선택 시 방향 분석이 표시됩니다.")
