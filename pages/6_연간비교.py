import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months
from core.metrics import calc_kpi

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
st.caption("연도별 손익 추세 — 원재료는 이달 매입 기준(재고 반영 전)")

months = get_available_months()
if not months:
    st.warning("데이터가 없습니다. 데이터 허브에서 분개장을 업로드해 주세요.")
    st.stop()

# 사용 가능한 연도 목록
all_years = sorted(set(m[:4] for m in months))


@st.cache_data(ttl=600)
def load_annual_kpi(year: str, month_list: list[str]) -> dict:
    """해당 연도의 월별 KPI를 합산해서 연간 KPI 반환."""
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
    kpi["적재월수"] = len(year_months)
    return kpi


# ── 연도 선택 ─────────────────────────────────────────────────────────────
sel_years = st.multiselect(
    "비교할 연도 선택 (최대 4개)",
    options=all_years,
    default=all_years[-3:] if len(all_years) >= 3 else all_years,
    max_selections=4,
)

if not sel_years:
    st.info("연도를 선택해 주세요.")
    st.stop()

# ── 연간 KPI 로드 ────────────────────────────────────────────────────────
with st.spinner("연간 데이터 집계 중..."):
    annual = {}
    for yr in sel_years:
        kpi = load_annual_kpi(yr, months)
        if kpi:
            annual[yr] = kpi

if not annual:
    st.error("선택한 연도의 데이터가 없습니다.")
    st.stop()

# 2022년 안내 (9개월)
if "2022" in annual and annual["2022"].get("적재월수", 12) < 12:
    months_2022 = annual["2022"]["적재월수"]
    st.info(f"ℹ️ 2022년은 {months_2022}개월 데이터 (4월~12월, 공장 취득 연도)")

# ── 손익 비교표 ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📊 손익 비교표")

ROWS = [
    ("매출액",      "매출액",      False),
    ("원재료매입",  "원재료순",    False),
    ("부재료매입",  "부재료매입",  False),
    ("인건비",      "인건비",      False),
    ("전력·수도",   None,          False),
    ("물류·차량",   None,          False),
    ("유지·소모품", None,          False),
    ("보험료",      None,          False),
    ("세금·임차",   None,          False),
    ("수수료",      None,          False),
    ("기타비용",    None,          False),
    ("영업이익",    "영업이익_v7", True),
    ("이자비용",    "이자비용",    False),
    ("실질이익",    "실질이익",    True),
]


def get_val(kpi: dict, label: str, key) -> float:
    if key is not None:
        return kpi.get(key, 0)
    # 비용대분류_v7에서 가져옴
    bucket = kpi.get("비용대분류_v7", {})
    mapping = {
        "전력·수도": "전력·수도",
        "물류·차량": "물류·차량",
        "유지·소모품": "유지·소모품",
        "보험료": "보험료",
        "세금·임차": "세금·임차",
        "수수료": "수수료",
    }
    if label in mapping:
        return bucket.get(mapping[label], 0)
    # 기타비용: 전체 운영비에서 이미 열거된 항목 제외 (일회성손익 제외)
    if label == "기타비용":
        known = set(mapping.values()) | {"인건비", "일회성손익"}
        return sum(v for k, v in bucket.items() if k not in known)
    return 0


table_data = {}
for label, key, is_profit in ROWS:
    row = {"항목": label}
    for yr in sel_years:
        kpi = annual[yr]
        val = get_val(kpi, label, key)
        매출 = kpi.get("매출액", 1) or 1
        pct = val / 매출 * 100
        row[f"{yr}년_금액"] = val
        row[f"{yr}년_%"] = pct
    table_data[label] = row

# 표시용 DataFrame 생성
display_rows = []
for label, key, is_profit in ROWS:
    row = table_data[label]
    disp = {"항목": label}
    for yr in sel_years:
        val = row[f"{yr}년_금액"]
        pct = row[f"{yr}년_%"]
        disp[f"{yr}년"] = f"{val/1e8:.2f}억  ({pct:.1f}%)"
    display_rows.append(disp)

# 연도간 영업이익률 증감
if len(sel_years) >= 2:
    yr_sorted = sorted(sel_years)
    diff_row = {"항목": "영업이익률 증감"}
    prev_yr = None
    for yr in yr_sorted:
        if prev_yr is None:
            diff_row[f"{yr}년"] = "—"
        else:
            cur_r = annual[yr].get("영업이익률_v7", 0)
            prv_r = annual[prev_yr].get("영업이익률_v7", 0)
            diff = cur_r - prv_r
            diff_row[f"{yr}년"] = f"{diff:+.1f}%p"
        prev_yr = yr
    display_rows.append(diff_row)

disp_df = pd.DataFrame(display_rows)
st.dataframe(disp_df, use_container_width=True, hide_index=True)

# ── 영업이익률 추세 바차트 ────────────────────────────────────────────────
st.markdown("---")
col_bar1, col_bar2 = st.columns(2)

with col_bar1:
    st.subheader("매출액 추이")
    fig = go.Figure()
    yr_sorted = sorted(annual.keys())
    fig.add_trace(go.Bar(
        x=[f"{y}년" for y in yr_sorted],
        y=[annual[y]["매출액"] / 1e8 for y in yr_sorted],
        marker_color="#60a5fa",
        text=[f"{annual[y]['매출액']/1e8:.1f}억" for y in yr_sorted],
        textposition="outside",
        name="매출액",
    ))
    fig.update_layout(
        height=280, margin=dict(t=20, b=10),
        yaxis_title="억원",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

with col_bar2:
    st.subheader("영업이익률 추이")
    colors = ["#4ade80" if annual[y].get("영업이익률_v7", 0) >= 0 else "#f87171"
              for y in yr_sorted]
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=[f"{y}년" for y in yr_sorted],
        y=[annual[y].get("영업이익률_v7", 0) for y in yr_sorted],
        marker_color=colors,
        text=[f"{annual[y].get('영업이익률_v7', 0):.1f}%" for y in yr_sorted],
        textposition="outside",
        name="영업이익률",
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="#6b7280")
    fig2.update_layout(
        height=280, margin=dict(t=20, b=10),
        yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"), showlegend=False,
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── 비용 구조 비교 ────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("비용 구조 비교 (매출대비 %)")

COST_ITEMS = ["원재료매입", "부재료매입", "인건비", "전력·수도", "물류·차량", "기타비용"]
COLORS = ["#fbbf24", "#fb923c", "#f87171", "#60a5fa", "#34d399", "#94a3b8"]

fig3 = go.Figure()
for label, color in zip(COST_ITEMS, COLORS):
    key = [r[1] for r in ROWS if r[0] == label][0]
    pcts = []
    for yr in yr_sorted:
        kpi = annual[yr]
        val = get_val(kpi, label, key)
        매출 = kpi.get("매출액", 1) or 1
        pcts.append(val / 매출 * 100)

    fig3.add_trace(go.Bar(
        name=label,
        x=[f"{y}년" for y in yr_sorted],
        y=pcts,
        marker_color=color,
        text=[f"{p:.1f}%" for p in pcts],
        textposition="inside",
        textfont_size=10,
    ))

fig3.update_layout(
    barmode="stack",
    height=350, margin=dict(t=20, b=10),
    yaxis_title="매출대비 %",
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig3, use_container_width=True)

# ── 인건비율 · 원재료율 추세 ──────────────────────────────────────────────
st.markdown("---")
col_l1, col_l2 = st.columns(2)

with col_l1:
    st.subheader("인건비율 추세")
    fig4 = go.Figure()
    labor_pcts = []
    for yr in yr_sorted:
        kpi = annual[yr]
        p = kpi.get("인건비", 0) / (kpi.get("매출액", 1) or 1) * 100
        labor_pcts.append(p)
    fig4.add_trace(go.Scatter(
        x=[f"{y}년" for y in yr_sorted],
        y=labor_pcts,
        mode="lines+markers+text",
        line=dict(color="#f87171", width=2),
        marker=dict(size=8),
        text=[f"{p:.1f}%" for p in labor_pcts],
        textposition="top center",
    ))
    fig4.update_layout(
        height=240, margin=dict(t=20, b=10),
        yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig4, use_container_width=True)

with col_l2:
    st.subheader("원재료매입율 추세")
    fig5 = go.Figure()
    mat_pcts = []
    for yr in yr_sorted:
        kpi = annual[yr]
        p = kpi.get("원재료순", 0) / (kpi.get("매출액", 1) or 1) * 100
        mat_pcts.append(p)
    fig5.add_trace(go.Scatter(
        x=[f"{y}년" for y in yr_sorted],
        y=mat_pcts,
        mode="lines+markers+text",
        line=dict(color="#fbbf24", width=2),
        marker=dict(size=8),
        text=[f"{p:.1f}%" for p in mat_pcts],
        textposition="top center",
    ))
    fig5.update_layout(
        height=240, margin=dict(t=20, b=10),
        yaxis_title="%",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig5, use_container_width=True)

# ── 방향 근거 ─────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 추세로 본 다음 해 방향")

if len(yr_sorted) >= 2:
    latest = annual[yr_sorted[-1]]
    prev = annual[yr_sorted[-2]]
    actions = []

    margin_chg = latest.get("영업이익률_v7", 0) - prev.get("영업이익률_v7", 0)
    labor_chg = (latest.get("인건비", 0) / (latest.get("매출액", 1) or 1) -
                 prev.get("인건비", 0) / (prev.get("매출액", 1) or 1)) * 100
    sales_chg = (latest.get("매출액", 0) - prev.get("매출액", 0)) / (prev.get("매출액", 1) or 1) * 100

    if sales_chg < -5:
        actions.append(f"📉 매출 {sales_chg:+.1f}% 감소 추세 — 신규 거래처 발굴 및 기존 거래처 이탈 방지")
    if margin_chg < -2:
        actions.append(f"⚠️ 영업이익률 {margin_chg:+.1f}%p 하락 — 원가 절감 또는 판가 인상 검토")
    if labor_chg > 1:
        actions.append(f"⚠️ 인건비율 {labor_chg:+.1f}%p 상승 — 인력 효율화 또는 외주 조정 검토")
    if latest.get("영업이익_v7", 0) < 0:
        actions.append("🚨 영업 적자 구조 — 고정비 구조 전면 재검토 필요")
    if not actions:
        actions.append("✅ 추세 양호 — 현 구조 유지하면서 성장 기회 모색")

    for a in actions:
        st.markdown(f"- {a}")
else:
    st.info("2개 연도 이상 선택 시 방향 분석이 표시됩니다.")
