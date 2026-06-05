import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import (
    load_raw_material_price, load_raw_material_summary,
    get_raw_material_months, RAW_MATERIAL_SQL,
)
from core.metrics import fmt_krw

st.set_page_config(page_title="원판 관리", page_icon="🪟", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("🪟 원판 관리")
st.caption("원판 구매단가 추이 · 수불 현황 | 데이터허브 → 원판 업로드 탭에서 파일 업로드")

# ── DB 테이블 생성 안내 ───────────────────────────────────────────────────
with st.expander("⚙️ 최초 설정: Supabase 테이블 생성"):
    st.caption("Supabase 대시보드 → SQL Editor에서 아래 SQL을 실행하세요. (최초 1회만)")
    st.code(RAW_MATERIAL_SQL, language="sql")

# ── 연월 선택 ─────────────────────────────────────────────────────────────
avail_months = get_raw_material_months()

if not avail_months:
    st.info(
        "아직 원판 데이터가 없습니다. "
        "**데이터 허브 → 원판 데이터 업로드** 탭에서 파일을 업로드해 주세요."
    )
    st.stop()

# (year, month) 목록
ym_options = [f"{y}-{m}" for y, m in avail_months]
sel_ym = st.selectbox("기준 연월", ym_options, index=0)
sel_year, sel_month = sel_ym[:4], sel_ym[5:7]

# ── 데이터 로드 ───────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_price_data(year, month):
    return load_raw_material_price(year, month)


@st.cache_data(ttl=600)
def load_summary_data():
    return load_raw_material_summary()


price_df = load_price_data(sel_year, sel_month)
summary_all = load_summary_data()

# ── 상단 집계 카드 ────────────────────────────────────────────────────────
st.markdown("---")
st.subheader(f"📦 {sel_year}년 {sel_month}월 수불 요약")

if not summary_all.empty:
    sel_summary = summary_all[
        (summary_all["year"].astype(str) == sel_year) &
        (summary_all["month"].astype(str) == sel_month)
    ]
    if not sel_summary.empty:
        row = sel_summary.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("당기 입고",
                  f"{int(row.get('당기입고_매', 0)):,}매",
                  delta=fmt_krw(row.get("당기입고_금액", 0)))
        c2.metric("당기 사용",
                  f"{int(row.get('당기사용_매', 0)):,}매",
                  delta=fmt_krw(row.get("당기사용_금액", 0)))
        c3.metric("기초 재고",
                  f"{int(row.get('기초재고_매', 0)):,}매",
                  delta=fmt_krw(row.get("기초재고_금액", 0)))
        c4.metric("기말 재고",
                  f"{int(row.get('기말재고_매', 0)):,}매",
                  delta=fmt_krw(row.get("기말재고_금액", 0)))
    else:
        st.info(f"{sel_ym} 수불 집계 데이터 없음")
else:
    st.info("수불 집계 데이터 없음")

# ── 단가 비교표 ───────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("💰 원판 단가 비교표")
st.caption("원/㎡ · 원/평 동시 표시 | 10% 이상 변동 품목 ⚠️ 경보")

if price_df.empty:
    st.info(f"{sel_ym} 단가 데이터 없음")
else:
    # 단가 변동 경보 (10% 이상)
    if "오기여부" in price_df.columns:
        오기 = price_df[price_df["오기여부"] == True]
        if not 오기.empty:
            st.warning(f"⚠️ 오기 의심 품목 {len(오기)}건 — 파일 계산값 vs 직접계산값 ±5원 초과")

    # 거래처 필터
    vendors = sorted(price_df["거래처"].dropna().unique()) if "거래처" in price_df.columns else []
    sel_vendor = st.selectbox("거래처 필터", ["전체"] + vendors)

    disp = price_df.copy()
    if sel_vendor != "전체" and "거래처" in disp.columns:
        disp = disp[disp["거래처"] == sel_vendor]

    # 표시용 가공
    disp_cols = []
    for col in ["거래처", "원산지", "두께", "규격자", "규격mm", "일자", "면적_m2", "금액_원", "원_m2", "원_평", "파일_원_m2", "파일_원_평", "오기여부"]:
        if col in disp.columns:
            disp_cols.append(col)

    if "금액_원" in disp.columns:
        disp["금액"] = disp["금액_원"].apply(fmt_krw)
    if "원_m2" in disp.columns:
        disp["원/㎡"] = disp["원_m2"].apply(lambda v: f"{v:,.0f}원")
    if "원_평" in disp.columns:
        disp["원/평"] = disp["원_평"].apply(lambda v: f"{v:,.0f}원")

    show_cols = [c for c in ["거래처", "원산지", "두께", "규격자", "일자", "금액", "원/㎡", "원/평", "오기여부"] if c in disp.columns]
    st.dataframe(disp[show_cols].sort_values(["두께", "거래처"] if "두께" in disp.columns else []),
                 use_container_width=True, hide_index=True)

    # 두께별 평균 단가 바차트
    if "두께" in price_df.columns and "원_m2" in price_df.columns:
        st.markdown("---")
        st.subheader("📊 두께별 평균 단가 (원/㎡)")
        avg_by_thick = (
            price_df.groupby(["두께", "거래처"])["원_m2"]
            .mean()
            .reset_index()
            .sort_values("두께")
        ) if "거래처" in price_df.columns else price_df.groupby("두께")["원_m2"].mean().reset_index()

        colors_map = {"KCC글라스": "#60a5fa", "LX글라스": "#34d399",
                      "한유에스앤지": "#fbbf24", "한성유엔씨": "#f87171"}

        fig = go.Figure()
        if "거래처" in avg_by_thick.columns:
            for vendor in avg_by_thick["거래처"].unique():
                sub = avg_by_thick[avg_by_thick["거래처"] == vendor]
                fig.add_trace(go.Bar(
                    name=vendor,
                    x=sub["두께"].astype(str) + "mm",
                    y=sub["원_m2"],
                    marker_color=colors_map.get(vendor, "#94a3b8"),
                    text=sub["원_m2"].apply(lambda v: f"{v:,.0f}"),
                    textposition="outside",
                ))
        else:
            fig.add_trace(go.Bar(
                x=avg_by_thick["두께"].astype(str) + "mm",
                y=avg_by_thick["원_m2"],
                marker_color="#60a5fa",
            ))

        fig.update_layout(
            barmode="group",
            xaxis_title="두께", yaxis_title="원/㎡",
            height=320, margin=dict(t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
        )
        st.plotly_chart(fig, use_container_width=True)

# ── 연간 탭: 거래처별 월별 매입금액 ──────────────────────────────────────
st.markdown("---")
st.subheader("📅 연간 매입금액 추이")

if not summary_all.empty:
    all_years = sorted(summary_all["year"].astype(str).unique(), reverse=True)
    sel_year_ann = st.selectbox("연도", all_years, index=0, key="ann_year")

    year_data = summary_all[summary_all["year"].astype(str) == sel_year_ann].copy()
    year_data = year_data.sort_values("month")

    if year_data.empty:
        st.info(f"{sel_year_ann}년 데이터 없음")
    else:
        month_labels = year_data["month"].apply(lambda m: f"{int(m):02d}월").tolist()

        fig_ann = go.Figure()
        if "당기입고_금액" in year_data.columns:
            fig_ann.add_trace(go.Bar(
                name="당기입고",
                x=month_labels,
                y=year_data["당기입고_금액"] / 1e6,
                marker_color="#60a5fa",
                text=(year_data["당기입고_금액"] / 1e6).apply(lambda v: f"{v:.0f}"),
                textposition="outside",
            ))
        if "당기사용_금액" in year_data.columns:
            fig_ann.add_trace(go.Bar(
                name="당기사용",
                x=month_labels,
                y=year_data["당기사용_금액"] / 1e6,
                marker_color="#f87171",
                text=(year_data["당기사용_금액"] / 1e6).apply(lambda v: f"{v:.0f}"),
                textposition="outside",
            ))

        fig_ann.update_layout(
            barmode="group",
            xaxis_title="월", yaxis_title="금액 (백만원)",
            height=300, margin=dict(t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
        )
        st.plotly_chart(fig_ann, use_container_width=True)

        # 연간 집계 테이블
        disp_cols = [c for c in ["month", "당기입고_매", "당기입고_금액",
                                  "당기사용_매", "당기사용_금액",
                                  "기초재고_매", "기말재고_매"] if c in year_data.columns]
        disp_ann = year_data[disp_cols].copy()
        disp_ann["month"] = disp_ann["month"].apply(lambda m: f"{int(m):02d}월")
        for col in ["당기입고_금액", "당기사용_금액"]:
            if col in disp_ann.columns:
                disp_ann[col] = disp_ann[col].apply(fmt_krw)
        st.dataframe(disp_ann, use_container_width=True, hide_index=True)
else:
    st.info("수불 집계 데이터 없음 — 데이터 허브에서 원판 데이터를 업로드하세요.")

st.markdown("---")
st.caption("💡 **다음달 챙길 일** — 단가 10% 이상 변동 품목 확인, 재고 적정 수준 유지")
