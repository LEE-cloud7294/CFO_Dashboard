import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months, get_annual_dep
from core.metrics import calc_kpi, _apply_cost_bucket, fmt_krw, apply_monthly_depreciation

st.set_page_config(page_title="손익 분석", page_icon="💰", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("💰 손익 분석")
st.caption("공장(5xx)·본사(8xx) 비용 대분류 분석 | 월/연간 토글")

months = get_available_months()
if not months:
    st.warning("데이터가 없습니다. 데이터 허브에서 분개장을 업로드해 주세요.")
    st.stop()

all_years = sorted(set(m[:4] for m in months), reverse=True)

# ── 월/연 선택 ────────────────────────────────────────────────────────────────
col_period, col_sel, col_tog = st.columns([1, 2, 2])
period_mode = col_period.radio("기간", ["월별", "연간"], horizontal=True)
display_mode = col_tog.radio("표시 방식", ["절대금액", "매출 대비 비율 (%)"], horizontal=True)

if period_mode == "월별":
    selected_ym = col_sel.selectbox("분석 월", months, index=0)
    selected_year = selected_ym[:4]
    title_label = f"{selected_ym[:4]}년 {selected_ym[5:7]}월"
else:
    selected_year = col_sel.selectbox("분석 연도", all_years, index=0)
    selected_ym = None
    title_label = f"{selected_year}년 연간"


@st.cache_data(ttl=300)
def load_data(ym):
    df = load_journal(ym)
    if df.empty:
        return df, {}
    df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return df, calc_kpi(df)


@st.cache_data(ttl=600)
def load_data_year(year: str, month_list: list[str]):
    year_months = [m for m in month_list if m.startswith(year)]
    frames = []
    for ym in year_months:
        df = load_journal(ym)
        if not df.empty:
            if "계정그룹" not in df.columns:
                df["계정그룹"] = df["계정코드"].astype(str).str[:1]
            frames.append(df)
    if not frames:
        return pd.DataFrame(), {}
    combined = pd.concat(frames, ignore_index=True)
    return combined, calc_kpi(combined)


@st.cache_data(ttl=3600)
def get_dep(year: str) -> float:
    return get_annual_dep(year)


# 데이터 로드
if period_mode == "월별":
    df, kpi = load_data(selected_ym)
else:
    with st.spinner(f"{selected_year}년 전체 데이터 집계 중..."):
        df, kpi = load_data_year(selected_year, months)

if df.empty:
    st.error("데이터 없음")
    st.stop()

st.caption(f"분석 기간: **{title_label}** | 분개행: {len(df):,}개")

# 감가상각 적용 (월별 ÷12, 연간은 연간 총액)
annual_dep = get_dep(selected_year)
dep_amount = (annual_dep / 12) if period_mode == "월별" else annual_dep
if annual_dep > 0:
    kpi = apply_monthly_depreciation(kpi, dep_amount)

매출액 = kpi["매출액"]

# ── 비용 대분류 집계 ──────────────────────────────────────────────────────────
# Code 501/502(연말 대체) 제외, Code 153/162 기준으로 원재료·부재료 계산
cost_df = df[df["계정그룹"].isin(["5", "8"])].copy()
cost_df["대분류"] = _apply_cost_bucket(cost_df)
cost_df = cost_df[cost_df["대분류"].notna()]
# 원재료·부재료·일회성손익 제외 (이중계산 방지)
cost_df_display = cost_df[~cost_df["대분류"].isin(["원재료", "부재료", "일회성손익"])]

# 원재료·부재료: Code 153/162 차변 (월별 매입 기준)
원재료매입 = df[df["계정코드"].astype(str) == "153"]["차변"].sum()
매입할인 = df[df["계정코드"].astype(str) == "155"]["대변"].sum()
원재료순 = max(원재료매입 - 매입할인, 0)
부재료매입 = df[df["계정코드"].astype(str) == "162"]["차변"].sum()

# Code 501 안내
code501 = df[df["계정코드"].astype(str) == "501"]["차변"].sum()
if code501 > 0:
    st.info(
        f"ℹ️ 연말 원재료 회계 대체(Code 501) {fmt_krw(code501)} 포함 — "
        "원재료 금액은 실제 매입(Code 153) 기준으로 표시됩니다."
    )

# 버킷 집계
bucket_base = (
    cost_df_display.groupby("대분류")["차변"]
    .sum()
    .reset_index()
    .rename(columns={"차변": "금액"})
)

# 원재료·부재료 추가
extra_rows = []
if 원재료순 > 0:
    extra_rows.append({"대분류": "원재료매입", "금액": 원재료순})
if 부재료매입 > 0:
    extra_rows.append({"대분류": "부재료매입", "금액": 부재료매입})
# 감가상각 월별 배분 추가
if dep_amount > 0:
    dep_label = "감가상각(월배분)" if period_mode == "월별" else "감가상각(연간)"
    extra_rows.append({"대분류": dep_label, "금액": dep_amount})

if extra_rows:
    bucket_base = pd.concat([bucket_base, pd.DataFrame(extra_rows)], ignore_index=True)

bucket_summary = bucket_base.sort_values("금액", ascending=False).reset_index(drop=True)
bucket_summary["비율(%)"] = (bucket_summary["금액"] / 매출액 * 100).round(1) if 매출액 > 0 else 0.0

# 표시 총비용 (실제 표시 기준 — Code 501 제외)
총비용_표시 = bucket_summary["금액"].sum()
인건비_금액 = bucket_summary[bucket_summary["대분류"] == "인건비"]["금액"].sum() if not bucket_summary.empty else 0

# ── 매출 계정 진단 ───────────────────────────────────────────────────────────
with st.expander("🔍 매출·비용 원시 데이터 진단"):
    acc4 = df[df["계정그룹"] == "4"][["계정과목", "차변", "대변"]].groupby("계정과목").sum()
    st.caption("4xx 계정 전체")
    st.dataframe(acc4.reset_index(), use_container_width=True)
    acc58 = df[df["계정그룹"].isin(["5","8"])][["계정그룹","계정과목","차변"]].groupby(["계정그룹","계정과목"]).sum()
    st.caption("5xx+8xx 계정 전체")
    st.dataframe(acc58.reset_index().rename(columns={"계정그룹":"그룹","차변":"금액"}), use_container_width=True)
    st.info(f"매출액: {fmt_krw(매출액)} | 5xx+8xx 합계(Code501 포함): {fmt_krw(cost_df['차변'].sum())}")

# ── KPI 요약 ────────────────────────────────────────────────────────────────
st.markdown("---")
k1, k2, k3, k4 = st.columns(4)
k1.metric("총비용 (표시 기준)", fmt_krw(총비용_표시))
if 매출액 > 0:
    k2.metric("비용/매출 비율", f"{총비용_표시/매출액*100:.1f}%")
    k3.metric("인건비율", f"{인건비_금액/매출액*100:.1f}%")
    k4.metric("영업이익률", f"{kpi.get('영업이익률_v7', 0):.1f}%")
else:
    k2.metric("비용/매출 비율", "N/A")
    k3.metric("인건비율", "N/A")
    k4.metric("영업이익률", "N/A")

if annual_dep > 0:
    if period_mode == "월별":
        st.caption(f"📊 감가상각 {fmt_krw(dep_amount)}/월 균등 배분 반영 (연간 {fmt_krw(annual_dep)})")
    else:
        st.caption(f"📊 감가상각 연간 {fmt_krw(annual_dep)} 반영")

# ── 메인 차트 ──────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("비용 대분류")

chart_data = bucket_summary.copy()
if display_mode == "절대금액":
    y_col = "금액"
    y_label = "금액 (원)"
    text_col = chart_data["금액"].apply(fmt_krw)
else:
    y_col = "비율(%)"
    y_label = "매출 대비 비율 (%)"
    text_col = chart_data["비율(%)"].apply(lambda v: f"{v:.1f}%")

fig = px.bar(
    chart_data, x=y_col, y="대분류", orientation="h",
    text=text_col, color=y_col, color_continuous_scale="Reds",
    labels={y_col: y_label},
)
fig.update_traces(textposition="outside")
fig.update_layout(
    height=max(320, len(chart_data) * 45),
    margin=dict(t=10, b=10, l=10, r=80),
    coloraxis_showscale=False,
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"), yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig, use_container_width=True)

# ── 대분류 테이블 ─────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("대분류별 상세")

display_df = bucket_summary[["대분류", "금액", "비율(%)"]].copy()
st.dataframe(
    display_df, use_container_width=True, hide_index=True,
    column_config={"금액": st.column_config.NumberColumn("금액", format="localized")},
)

# ── Drill-down ───────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("상세 Drill-down")
RAW_MATERIAL_CODE = {"원재료매입": "153", "부재료매입": "162"}
all_buckets = [b for b in bucket_summary["대분류"].tolist()
               if b not in ("감가상각(월배분)", "감가상각(연간)")]
selected_bucket = st.selectbox("대분류 선택", ["선택하세요"] + all_buckets)

if selected_bucket != "선택하세요":
    target_bucket = selected_bucket
    if target_bucket in RAW_MATERIAL_CODE:
        # 원재료·부재료는 cost_df에서 제외된 별도 계정(153/162) — 분개장에서 직접 거래처별 집계
        detail = df[df["계정코드"].astype(str) == RAW_MATERIAL_CODE[target_bucket]].copy()
        group_col = "거래처"
    else:
        # "일회성손익" bucket은 이미 cost_df에서 제외됨 — cost_df_display 기준
        detail = cost_df_display[cost_df_display["대분류"] == target_bucket].copy()
        group_col = "계정과목"

    if detail.empty:
        st.info(f"'{selected_bucket}' 상세 전표 없음")
    else:
        by_group = (
            detail.groupby(group_col)["차변"]
            .sum().reset_index()
            .rename(columns={"차변": "금액"})
            .sort_values("금액", ascending=False)
        )
        col_a, col_b = st.columns([1, 2])
        with col_a:
            label = "거래처별" if group_col == "거래처" else "계정별"
            st.markdown(f"**{selected_bucket} — {label} 집계**")
            st.dataframe(
                by_group, use_container_width=True, hide_index=True,
                column_config={"금액": st.column_config.NumberColumn("금액", format="localized")},
            )
        with col_b:
            st.markdown(f"**{selected_bucket} — 전표 상세**")
            show = detail[["전표일자","계정과목","거래처","적요","차변"]].sort_values("차변", ascending=False).head(30)
            st.dataframe(
                show, use_container_width=True, hide_index=True,
                column_config={"차변": st.column_config.NumberColumn("차변", format="localized")},
            )

# ── 공장 vs 본사 ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("공장(제조원가) vs 본사(판관비)")

factory = cost_df_display[cost_df_display["계정그룹"] == "5"]["차변"].sum() if "계정그룹" in cost_df_display.columns else 0
hq = cost_df_display[cost_df_display["계정그룹"] == "8"]["차변"].sum() if "계정그룹" in cost_df_display.columns else 0

if factory + hq > 0:
    fig2 = go.Figure(go.Pie(
        labels=["공장 (제조원가 5xx)", "본사 (판관비 8xx)"],
        values=[factory, hq],
        hole=0.45, marker_colors=["#ef5350", "#ff9800"],
        textinfo="label+percent",
    ))
    fig2.update_layout(
        height=280, margin=dict(t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── 다음 달 원가 관리 포인트 ─────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 다음 달 원가 관리 포인트")
tops = bucket_summary.head(3)["대분류"].tolist()
for t in tops:
    row = bucket_summary[bucket_summary["대분류"] == t].iloc[0]
    st.markdown(f"- **{t}** {row['비율(%)']:.1f}% ({fmt_krw(row['금액'])}) — 전월 대비 증감 확인 필요")
