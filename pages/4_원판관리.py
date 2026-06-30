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

# 거래처 색상 팔레트 (전역)
VENDOR_PALETTE = [
    "#60a5fa",  # 파란색  (KCC)
    "#34d399",  # 녹색   (LX)
    "#fbbf24",  # 노란색 (한유)
    "#f87171",  # 빨간색 (한성)
    "#a78bfa",  # 보라색 (KGT)
    "#fb923c",  # 주황색
]

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


@st.cache_data(ttl=600)
def load_all_price_data(ym_list: list) -> pd.DataFrame:
    """전체 연월 단가 데이터 합산 로드."""
    frames = []
    for y, m in ym_list:
        df_tmp = load_raw_material_price(y, m)
        if not df_tmp.empty:
            frames.append(df_tmp)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


price_df = load_price_data(sel_year, sel_month)
summary_all = load_summary_data()
all_price_df = load_all_price_data(avail_months)   # 전체 기간 — 추이·상세에서 공유

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

def _calc_pyeong(row):
    """원/평 = 원/m² ÷ 10.764 (유리업계: 평 = ft²).
    DB 원_평값을 신뢰하지 않고 항상 원_m2에서 계산 (col 인덱스 오류 대응).
    """
    m2 = row.get("원_m2", 0) or 0
    return round(m2 / 10.764) if m2 > 0 else 0


if price_df.empty:
    st.info(f"{sel_ym} 단가 데이터 없음")
else:
    # 원_평 보정: 0인 경우 원_m2에서 환산
    price_df = price_df.copy()
    price_df["원_평_표시"] = price_df.apply(_calc_pyeong, axis=1)

    # 단가 변동 경보
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

    if "금액_원" in disp.columns:
        disp["금액"] = disp["금액_원"]
    if "원_평_표시" in disp.columns:
        disp["원/평"] = disp["원_평_표시"]
    if "원_m2" in disp.columns:
        disp["원/㎡"] = disp["원_m2"]

    show_cols = [c for c in ["거래처", "원산지", "두께", "규격자", "일자", "금액", "원/평", "원/㎡", "오기여부"] if c in disp.columns]
    st.dataframe(
        disp[show_cols].sort_values(["두께", "거래처"] if "두께" in disp.columns else []),
        use_container_width=True, hide_index=True,
        column_config={
            "금액": st.column_config.NumberColumn("금액", format="localized"),
            "원/평": st.column_config.NumberColumn("원/평", format="localized"),
            "원/㎡": st.column_config.NumberColumn("원/㎡", format="localized"),
        },
    )
    st.caption("원/평 = 원/㎡ ÷ 10.764 (유리업계 단위: 평 = 1ft²)")

    # 두께별 평균 단가 바차트 — 원/평 기준
    if "두께" in price_df.columns and "원_평_표시" in price_df.columns:
        st.markdown("---")
        st.subheader("📊 두께별 평균 단가 (원/평)")
        st.caption("같은 두께의 모든 사이즈를 통합한 거래처별 평균 | 원/평 = 원/㎡ ÷ 10.764")

        avg_src = price_df[price_df["원_평_표시"] > 0]
        avg_by_thick = (
            avg_src.groupby(["두께", "거래처"])["원_평_표시"]
            .mean()
            .reset_index()
            .sort_values("두께")
        ) if "거래처" in avg_src.columns else (
            avg_src.groupby("두께")["원_평_표시"].mean().reset_index()
        )

        vendors_sorted = sorted(avg_by_thick["거래처"].unique()) if "거래처" in avg_by_thick.columns else []
        vendor_color = {v: VENDOR_PALETTE[i % len(VENDOR_PALETTE)]
                        for i, v in enumerate(vendors_sorted)}

        fig = go.Figure()
        if "거래처" in avg_by_thick.columns:
            for vendor in vendors_sorted:
                sub = avg_by_thick[avg_by_thick["거래처"] == vendor]
                if sub.empty:
                    continue
                fig.add_trace(go.Bar(
                    name=vendor,
                    x=sub["두께"].astype(str) + "mm",
                    y=sub["원_평_표시"],
                    marker_color=vendor_color[vendor],
                    text=sub["원_평_표시"].apply(lambda v: f"{int(v):,}원"),
                    textposition="outside",
                    textfont=dict(size=11),
                ))
        else:
            fig.add_trace(go.Bar(
                x=avg_by_thick["두께"].astype(str) + "mm",
                y=avg_by_thick["원_평_표시"],
                marker_color="#60a5fa",
                text=avg_by_thick["원_평_표시"].apply(lambda v: f"{int(v):,}원"),
                textposition="outside",
            ))

        fig.update_layout(
            barmode="group",
            xaxis_title="두께 (mm)",
            yaxis=dict(title="원/평", tickformat=",.0f", ticksuffix="원"),
            height=380,
            margin=dict(t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        )
        st.plotly_chart(fig, use_container_width=True)

# ── 월별 단가 추이 (꺾은선) ───────────────────────────────────────────────
st.markdown("---")
st.subheader("📈 월별 단가 추이 (원/평)")
st.caption("거래처별 월평균 단가 변화 — 가격 상승/하락 추적 | 원/평 = 원/㎡ ÷ 10.764")

if not all_price_df.empty and "원_m2" in all_price_df.columns:
    trend_years = sorted(
        all_price_df["year"].astype(str).unique(), reverse=True
    ) if "year" in all_price_df.columns else []

    if trend_years:
        tcol1, tcol2 = st.columns([1, 2])
        sel_trend_year = tcol1.selectbox("연도", trend_years, index=0, key="trend_year")

        # 두께 필터
        thick_vals = sorted([
            int(v) for v in all_price_df["두께"].dropna().unique()
            if str(v).replace(".0", "").isdigit()
        ]) if "두께" in all_price_df.columns else []
        thick_filter_opts = ["전체"] + [f"{t}mm" for t in thick_vals]
        sel_trend_thick = tcol2.selectbox("두께 필터", thick_filter_opts, index=0, key="trend_thick")

        # 해당 연도 데이터 필터 + 원_평 보정
        trend_df = all_price_df[
            all_price_df["year"].astype(str) == sel_trend_year
        ].copy()
        trend_df["원_평_표시"] = trend_df.apply(_calc_pyeong, axis=1)
        trend_df = trend_df[trend_df["원_평_표시"] > 0]

        if sel_trend_thick != "전체":
            t_val = int(sel_trend_thick.replace("mm", ""))
            if "두께" in trend_df.columns:
                trend_df = trend_df[trend_df["두께"] == t_val]

        if trend_df.empty:
            st.info(f"{sel_trend_year}년 단가 데이터 없음")
        else:
            # 월 + 거래처별 평균 원/평
            if "month" in trend_df.columns and "거래처" in trend_df.columns:
                monthly_avg = (
                    trend_df.groupby(["month", "거래처"])["원_평_표시"]
                    .mean()
                    .reset_index()
                )
                monthly_avg["month_int"] = monthly_avg["month"].astype(str).str.zfill(2).astype(int)
                monthly_avg = monthly_avg.sort_values("month_int")
                monthly_avg["월"] = monthly_avg["month_int"].apply(lambda m: f"{m:02d}월")

                vendors_trend = sorted(monthly_avg["거래처"].unique())
                vendor_color_t = {v: VENDOR_PALETTE[i % len(VENDOR_PALETTE)]
                                  for i, v in enumerate(sorted(
                                      all_price_df["거래처"].unique() if "거래처" in all_price_df.columns else []
                                  ))}

                fig_trend = go.Figure()
                for vendor in vendors_trend:
                    sub = monthly_avg[monthly_avg["거래처"] == vendor].sort_values("month_int")
                    if sub.empty:
                        continue
                    color = vendor_color_t.get(vendor, "#94a3b8")
                    fig_trend.add_trace(go.Scatter(
                        x=sub["월"],
                        y=sub["원_평_표시"],
                        mode="lines+markers+text",
                        name=vendor,
                        line=dict(color=color, width=2),
                        marker=dict(size=8, color=color),
                        text=sub["원_평_표시"].apply(lambda v: f"{int(v):,}"),
                        textposition="top center",
                        textfont=dict(size=10),
                    ))

                thick_label = f" ({sel_trend_thick})" if sel_trend_thick != "전체" else " (전 두께 평균)"
                fig_trend.update_layout(
                    title=dict(
                        text=f"{sel_trend_year}년 월별 원/평 단가 추이{thick_label}",
                        font=dict(size=14, color="white"),
                        x=0,
                    ),
                    xaxis_title="월",
                    yaxis=dict(
                        title="원/평",
                        tickformat=",.0f",
                        ticksuffix="원",
                    ),
                    height=420,
                    margin=dict(t=50, b=40, l=20, r=20),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="left", x=0),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_trend, use_container_width=True)

                # 월별 단가 피벗 표 (원/평)
                if len(monthly_avg["월"].unique()) >= 2:
                    st.markdown("**월별 원/평 단가 변동**")
                    pivot = monthly_avg.pivot(index="거래처", columns="월", values="원_평_표시")
                    cols_sorted = sorted(pivot.columns, key=lambda x: int(x.replace("월", "")))
                    pivot = pivot[cols_sorted]
                    pivot_disp = pivot.applymap(lambda v: f"{int(v):,}원" if pd.notna(v) and v > 0 else "—")
                    st.dataframe(pivot_disp, use_container_width=True)
    else:
        st.info("단가 데이터 없음")
else:
    st.info("구매내역 데이터 없음 — 데이터 허브에서 업로드 후 확인하세요.")

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
        money_cols_ann = [c for c in ["당기입고_금액", "당기사용_금액"] if c in disp_ann.columns]
        st.dataframe(
            disp_ann, use_container_width=True, hide_index=True,
            column_config={c: st.column_config.NumberColumn(c, format="localized") for c in money_cols_ann},
        )
else:
    st.info("수불 집계 데이터 없음 — 데이터 허브에서 원판 데이터를 업로드하세요.")

# ── 거래처별 일자별 상세 ─────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 거래처별 일자별 구매 상세")
st.caption("전체 저장된 데이터 기준 | 연월 · 거래처 · 두께 필터 적용 가능")

if all_price_df.empty:
    st.info("상세 데이터 없음")
else:
    # 필터 UI
    fcol1, fcol2, fcol3 = st.columns(3)

    # 연월 필터
    ym_all = sorted(set(f"{r['year']}-{str(r['month']).zfill(2)}"
                        for _, r in all_price_df.iterrows()
                        if "year" in all_price_df.columns and "month" in all_price_df.columns),
                    reverse=True)
    sel_ym_detail = fcol1.selectbox("연월", ["전체"] + ym_all, key="detail_ym")

    # 거래처 필터
    vendors_all = sorted(all_price_df["거래처"].dropna().unique()) if "거래처" in all_price_df.columns else []
    sel_vendor_detail = fcol2.selectbox("거래처", ["전체"] + vendors_all, key="detail_vendor")

    # 두께 필터
    thick_all = sorted([int(v) for v in all_price_df["두께"].dropna().unique()
                        if str(v).replace(".0","").isdigit()]) if "두께" in all_price_df.columns else []
    thick_opts = [str(t) + "mm" for t in thick_all]
    sel_thick = fcol3.selectbox("두께", ["전체"] + thick_opts, key="detail_thick")

    detail_df = all_price_df.copy()

    if sel_ym_detail != "전체":
        y_f, m_f = sel_ym_detail[:4], sel_ym_detail[5:7]
        detail_df = detail_df[
            (detail_df["year"].astype(str) == y_f) &
            (detail_df["month"].astype(str).str.zfill(2) == m_f)
        ]
    if sel_vendor_detail != "전체" and "거래처" in detail_df.columns:
        detail_df = detail_df[detail_df["거래처"] == sel_vendor_detail]
    if sel_thick != "전체" and "두께" in detail_df.columns:
        t_val = int(sel_thick.replace("mm", ""))
        detail_df = detail_df[detail_df["두께"] == t_val]

    # 정렬: 연월 → 거래처 → 일자
    sort_cols = [c for c in ["year", "month", "거래처", "일자", "두께"] if c in detail_df.columns]
    if sort_cols:
        detail_df = detail_df.sort_values(sort_cols)

    # 표시용 가공
    detail_disp = detail_df.copy()
    # 원_평 보정 (0이면 원_m2에서 환산)
    detail_disp["원_평_표시"] = detail_disp.apply(_calc_pyeong, axis=1)

    if "금액_원" in detail_disp.columns:
        detail_disp["금액(부가세제외)"] = detail_disp["금액_원"]
    detail_disp["부가세(10%)"] = detail_disp.apply(
        lambda r: r["부가세_원"] if r.get("부가세_원", 0) > 0
                  else round(r.get("금액_원", 0) * 0.1), axis=1
    )
    if "합계_원" in detail_disp.columns:
        detail_disp["합계(부가세포함)"] = detail_disp["합계_원"]
    elif "금액_원" in detail_disp.columns:
        detail_disp["합계(부가세포함)"] = detail_disp["금액_원"].apply(lambda v: round(v * 1.1))
    # 원/평 (보정값), 원/㎡ 모두 표시
    detail_disp["원/평"] = detail_disp["원_평_표시"]
    if "원_m2" in detail_disp.columns:
        detail_disp["원/㎡"] = detail_disp["원_m2"]
    if "면적_m2" in detail_disp.columns:
        detail_disp["면적(㎡)"] = detail_disp["면적_m2"]

    # 연월 컬럼 합산
    if "year" in detail_disp.columns and "month" in detail_disp.columns:
        detail_disp["연월"] = detail_disp["year"].astype(str) + "-" + detail_disp["month"].astype(str).str.zfill(2)

    show_cols = [c for c in ["연월", "일자", "거래처", "원산지", "두께", "규격자", "규격mm",
                              "면적(㎡)", "원/평", "원/㎡",
                              "금액(부가세제외)", "부가세(10%)", "합계(부가세포함)", "오기여부"]
                 if c in detail_disp.columns]

    st.dataframe(
        detail_disp[show_cols], use_container_width=True, hide_index=True,
        height=min(400, max(200, len(detail_disp) * 36 + 40)),
        column_config={
            "면적(㎡)": st.column_config.NumberColumn("면적(㎡)", format="%.3f"),
            "원/평": st.column_config.NumberColumn("원/평", format="localized"),
            "원/㎡": st.column_config.NumberColumn("원/㎡", format="localized"),
            "금액(부가세제외)": st.column_config.NumberColumn("금액(부가세제외)", format="localized"),
            "부가세(10%)": st.column_config.NumberColumn("부가세(10%)", format="localized"),
            "합계(부가세포함)": st.column_config.NumberColumn("합계(부가세포함)", format="localized"),
        },
    )

    # 거래처별 합산 소계
    if not detail_df.empty and "거래처" in detail_df.columns and "금액_원" in detail_df.columns:
        st.markdown("**거래처별 합산**")
        subtotal = (
            detail_df.groupby("거래처")["금액_원"]
            .sum()
            .sort_values(ascending=False)
            .reset_index()
        )
        subtotal["금액"] = subtotal["금액_원"]
        if "원_m2" in detail_df.columns:
            avg_price = detail_df.groupby("거래처")["원_m2"].mean().round(0).astype(int)
            subtotal["평균원/㎡"] = subtotal["거래처"].map(avg_price)
        st.dataframe(
            subtotal[[c for c in ["거래처", "금액", "평균원/㎡"] if c in subtotal.columns]],
            use_container_width=True, hide_index=True,
            column_config={
                "금액": st.column_config.NumberColumn("금액", format="localized"),
                "평균원/㎡": st.column_config.NumberColumn("평균원/㎡", format="localized"),
            },
        )

st.markdown("---")
st.caption("💡 **다음달 챙길 일** — 단가 10% 이상 변동 품목 확인, 재고 적정 수준 유지")
