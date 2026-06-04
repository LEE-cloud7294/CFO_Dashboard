import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months
from core.metrics import calc_kpi, calc_cost_detail, cost_bucket, _apply_cost_bucket

st.set_page_config(page_title="원가·비용 통제", page_icon="💰", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("💰 원가 및 고정비 통제")
st.caption("공장(5xx)·본사(8xx) 비용을 대분류별로 분석합니다.")

months = get_available_months()
if not months:
    st.warning("데이터가 없습니다. 데이터 허브에서 분개장을 업로드해 주세요.")
    st.stop()

col_sel1, col_sel2, col_tog = st.columns([1, 1, 2])
selected_ym = col_sel1.selectbox("분석 월", months, index=0)
display_mode = col_tog.radio("표시 방식", ["절대금액 (원)", "매출 대비 비율 (%)"], horizontal=True)

@st.cache_data(ttl=300)
def load_data(ym):
    df = load_journal(ym)
    if df.empty:
        return df, {}
    df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return df, calc_kpi(df)

df, kpi = load_data(selected_ym)
if df.empty:
    st.error("해당 월 데이터 없음")
    st.stop()

매출액 = kpi["매출액"]

# ── 매출 계정 진단 ─────────────────────────────────────────────────────────
with st.expander("🔍 매출·비용 원시 데이터 진단 (이상수치 확인 시)"):
    # 4xx 계정 전체
    acc4 = df[df["계정그룹"] == "4"][["계정과목", "차변", "대변"]].groupby("계정과목").sum()
    acc4["비고"] = acc4.index.map(
        lambda x: "✅ 매출로 포함 (Code 404)" if ("매출" in str(x) and not any(k in str(x) for k in ["원가","에누리","환입","할인"]))
                  else "❌ 제외 (Code 404만 매출)"
    )
    st.caption("4xx 계정 전체 (매출로 잡히는 계정 확인)")
    st.dataframe(acc4.reset_index(), use_container_width=True)

    # 5xx + 8xx 계정 전체 합계
    acc58 = df[df["계정그룹"].isin(["5","8"])][["계정그룹","계정과목","차변"]].groupby(["계정그룹","계정과목"]).sum()
    st.caption("5xx+8xx 계정 전체 (비용으로 잡히는 계정 확인)")
    st.dataframe(acc58.reset_index().rename(columns={"계정그룹":"그룹","차변":"금액"}), use_container_width=True)

    st.info(
        f"**매출액 합계:** {매출액:,.0f}원 | "
        f"**총비용 합계:** {kpi['총비용']:,.0f}원"
    )
    if 매출액 == 0:
        st.error("⚠️ 이 월의 매출액이 0원입니다. 4xx 계정에 '매출'이 포함된 계정이 없거나 대변이 없습니다.")
    elif 매출액 < 10_000_000:
        st.warning(f"⚠️ 매출액이 {매출액/1e4:.0f}만원으로 매우 작습니다. 비율이 극단적으로 나타납니다 (창업 초기 정상).")

# ── 비용 대분류 집계 ──────────────────────────────────────────────────────
cost_df = df[df["계정그룹"].isin(["5", "8"])].copy()
cost_df["대분류"] = _apply_cost_bucket(cost_df)
cost_df = cost_df[cost_df["대분류"].notna()]  # 감가상각누계(None) 제거

bucket_summary = (
    cost_df.groupby("대분류")["차변"]
    .sum()
    .reset_index()
    .rename(columns={"차변": "금액"})
    .sort_values("금액", ascending=False)
)
bucket_summary["비율(%)"] = (bucket_summary["금액"] / 매출액 * 100).round(1) if 매출액 > 0 else 0.0
bucket_summary["금액(만원)"] = (bucket_summary["금액"] / 1e4).round(0)

운영_summary = bucket_summary.copy()

# ── 검증 배너 (1월 데이터) ────────────────────────────────────────────────
if selected_ym == "2026-01":
    인건비행 = bucket_summary[bucket_summary["대분류"] == "인건비"]
    if not 인건비행.empty:
        인건비 = 인건비행["금액"].values[0]
        if abs(인건비 - 198054270) < 1000:
            st.success("검증 통과: 인건비 198,054,270원 일치")
        else:
            st.warning(f"인건비 검증 불일치: {인건비:,.0f}원 (기준 198,054,270원)")

# ── 메인 차트 ─────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("비용 대분류")

chart_data = 운영_summary.copy()
if display_mode == "절대금액 (원)":
    y_col = "금액"
    y_label = "금액 (원)"
    text_col = chart_data["금액"].apply(lambda v: f"{v/1e8:.2f}억")
else:
    y_col = "비율(%)"
    y_label = "매출 대비 비율 (%)"
    text_col = chart_data["비율(%)"].apply(lambda v: f"{v:.1f}%")

fig = px.bar(
    chart_data,
    x=y_col,
    y="대분류",
    orientation="h",
    text=text_col,
    color=y_col,
    color_continuous_scale="Reds",
    labels={y_col: y_label},
)
fig.update_traces(textposition="outside")
fig.update_layout(
    height=max(300, len(chart_data) * 45),
    margin=dict(t=10, b=10, l=10, r=60),
    coloraxis_showscale=False,
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig, use_container_width=True)

# ── KPI 요약 ─────────────────────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)
k1.metric("총비용", f"{kpi['총비용']/1e8:.2f}억원")
if 매출액 > 0:
    k2.metric("비용/매출 비율", f"{kpi['총비용']/매출액*100:.1f}%")
    k3.metric("인건비율", f"{kpi['인건비율']:.1f}%")
    k4.metric("영업이익률", f"{kpi['영업이익률']:.1f}%")
else:
    k2.metric("비용/매출 비율", "N/A (매출 없음)")
    k3.metric("인건비율", "N/A")
    k4.metric("영업이익률", "N/A")

# ── 대분류 테이블 ─────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("대분류별 상세")

display_df = 운영_summary[["대분류", "금액", "비율(%)"]].copy()
display_df["금액"] = display_df["금액"].apply(lambda v: f"{v:,.0f}원")
st.dataframe(display_df, use_container_width=True, hide_index=True)

# ── Drill-down: 대분류 클릭 → 세부 계정 ──────────────────────────────────
st.markdown("---")
st.subheader("상세 Drill-down")
all_buckets = bucket_summary["대분류"].tolist()
selected_bucket = st.selectbox(
    "대분류 선택 (클릭하면 세부 계정 및 적요 표시)",
    ["선택하세요"] + all_buckets,
)

if selected_bucket != "선택하세요":
    detail = cost_df[cost_df["대분류"] == selected_bucket].copy()

    # 계정과목별 집계
    by_account = (
        detail.groupby("계정과목")["차변"]
        .sum()
        .reset_index()
        .rename(columns={"차변": "금액"})
        .sort_values("금액", ascending=False)
    )

    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.markdown(f"**{selected_bucket} — 계정별 집계**")
        by_account["금액"] = by_account["금액"].apply(lambda v: f"{v:,.0f}원")
        st.dataframe(by_account, use_container_width=True, hide_index=True)

    with col_b:
        st.markdown(f"**{selected_bucket} — 전표 상세 (적요)**")
        cols = ["전표일자", "계정과목", "거래처", "적요", "차변"]
        show = detail[cols].sort_values("차변", ascending=False).head(30)
        show["차변"] = show["차변"].apply(lambda v: f"{v:,.0f}")
        st.dataframe(show, use_container_width=True, hide_index=True)

# ── 공장(5xx) vs 본사(8xx) 분리 ─────────────────────────────────────────
st.markdown("---")
st.subheader("공장(제조원가) vs 본사(판관비)")

factory = df[df["계정그룹"] == "5"]["차변"].sum()
hq = df[df["계정그룹"] == "8"]["차변"].sum()

fig2 = go.Figure(go.Pie(
    labels=["공장 (제조원가 5xx)", "본사 (판관비 8xx)"],
    values=[factory, hq],
    hole=0.45,
    marker_colors=["#ef5350", "#ff9800"],
    textinfo="label+percent",
))
fig2.update_layout(
    height=280,
    margin=dict(t=10, b=10),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
)
st.plotly_chart(fig2, use_container_width=True)

# ── 다음 달 행동 제안 ─────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 다음 달 원가 관리 포인트")
tops = bucket_summary.head(3)["대분류"].tolist()
for t in tops:
    row = bucket_summary[bucket_summary["대분류"] == t].iloc[0]
    st.markdown(f"- **{t}** ({row['비율(%)']:.1f}%) — 전월 대비 증감 확인 필요")
