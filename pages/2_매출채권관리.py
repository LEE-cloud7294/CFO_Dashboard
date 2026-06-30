import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
import calendar
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import (load_journal_upto, get_available_months, load_master_blacklist,
                     is_erp_ar_verified, set_erp_ar_verified)
from core.aging import (calc_aging, calc_ar_summary, calc_concentration,
                        calc_overdue_ratio, extract_ar_collections,
                        calc_payment_pattern, calc_bills_receivable,
                        calc_partner_deep)
from core.metrics import fmt_krw

st.set_page_config(page_title="매출채권 관리", page_icon="📋", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("📋 매출채권 · 여신 집중 관리")
st.caption("외상매출금(108) — 창업일부터 선택월 말일까지 전체 누적 이력 기준 FIFO 연령분석")

# ── ERP 대조 신뢰도 안내 ────────────────────────────────────────────────
_erp_verified = is_erp_ar_verified()

if not _erp_verified:
    col_warn, col_btn = st.columns([4, 1])
    col_warn.warning(
        "⚠️ **ERP 미수금 대조 전** — "
        "잔액·회수율·DSO는 참고용입니다. "
        "마지막입금일·경과일은 신뢰 가능."
    )
    if col_btn.button("✅ ERP 대조 완료", type="primary"):
        set_erp_ar_verified(True)
        st.success("ERP 대조 완료로 표시했습니다.")
        st.rerun()
else:
    col_info, col_reset = st.columns([4, 1])
    col_info.success("✅ ERP 미수금 대조 완료 — 수치 신뢰 가능")
    if col_reset.button("↩ 대조 취소"):
        set_erp_ar_verified(False)
        st.rerun()

months = get_available_months()
if not months:
    st.warning("데이터가 없습니다. 데이터 허브에서 분개장을 업로드해 주세요.")
    st.stop()

col_sel, col_bl = st.columns([2, 2])
selected_ym = col_sel.selectbox("기준 월 (해당 월 말일 잔액 기준)", months, index=0)
hide_blacklist = col_bl.checkbox("블랙리스트 업체 숨기기", value=True)

year, month = int(selected_ym[:4]), int(selected_ym[5:7])
last_day = calendar.monthrange(year, month)[1]
as_of = date(year, month, last_day)


@st.cache_data(ttl=300)   # 5분 — 재업로드 후 빠른 반영
def load_cumulative(ym: str):
    df = load_journal_upto(ym)
    if not df.empty and "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return df


@st.cache_data(ttl=3600)
def get_blacklist():
    return load_master_blacklist()


with st.spinner(f"{selected_ym} 말일 기준 전체 누적 데이터 불러오는 중..."):
    df = load_cumulative(selected_ym)

if df.empty:
    st.error("데이터 없음")
    st.stop()

st.info(
    f"기준일: **{as_of.strftime('%Y년 %m월 %d일')}** | "
    f"누적 분개행: **{len(df):,}개** | "
    f"기간: {df['전표일자'].min().strftime('%Y-%m-%d')} ~ {df['전표일자'].max().strftime('%Y-%m-%d')}"
)

# ── 연령분석 + AR 요약 계산 ──────────────────────────────────────────────
aging_df = calc_aging(df, as_of=as_of)
summary_df = calc_ar_summary(df)
conc = calc_concentration(aging_df)
ratio = calc_overdue_ratio(aging_df)

if hide_blacklist:
    bl = get_blacklist()
    if bl:
        aging_df = aging_df[~aging_df["거래처"].isin(bl)]
        summary_df = summary_df[~summary_df["거래처"].isin(bl)]

# ── 108계정 진단 ────────────────────────────────────────────────────────
with st.expander("🔍 외상매출금(108) 원시 데이터 진단"):
    ar_raw = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    if ar_raw.empty:
        st.warning("⚠️ 108(외상매출금) 계정 데이터가 없습니다.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("108 총 행수", f"{len(ar_raw):,}개")
        c2.metric("발생 합계", fmt_krw(ar_raw['차변'].sum()))
        c3.metric("회수 합계", fmt_krw(ar_raw['대변'].sum()))
        c4.metric("순잔액", fmt_krw(ar_raw['차변'].sum()-ar_raw['대변'].sum()))
        neg_cnt = (ar_raw["차변"] < 0).sum()
        if neg_cnt > 0:
            st.warning(f"⚠️ 차변 음수(정정전표): {neg_cnt}건 감지 — FIFO에서 자동 처리됨. ERP 대조로 검증 필요.")
        blank_cr = ar_raw[(ar_raw["대변"]>0) & (ar_raw["거래처"].astype(str).str.strip()=="")]
        if len(blank_cr) > 0:
            st.warning(f"⚠️ 회수 행 중 거래처 공란: {len(blank_cr)}개 → 전표번호로 자동 보완 적용")

# ── 장기 미입금 경보 ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🚨 장기 미입금 거래처 즉시 확인")
st.caption("경과일 180일 이상 거래처 | ⚠️ = 정정전표 있어 잔액 검증 필요")

if not aging_df.empty and "경과일" in aging_df.columns:
    long_overdue = aging_df[
        (aging_df["잔액"] > 0) & (aging_df["경과일"].notna()) & (aging_df["경과일"] >= 180)
    ].sort_values("경과일", ascending=False).head(10)

    if not long_overdue.empty:
        for _, row in long_overdue.iterrows():
            days = int(row["경과일"])
            bal = fmt_krw(row["잔액"])
            corr = " ⚠️정정전표" if row.get("정정전표", False) else ""
            악성amt = row.get("악성(91+)", 0)
            악성_txt = f" | 91일+ {fmt_krw(악성amt)}" if 악성amt > 0 else ""
            st.error(f"🔴 **{row['거래처']}**{corr} — 잔액 {bal} | 마지막 입금 {days}일 전{악성_txt}")
    else:
        st.success("✅ 180일 이상 경과 거래처 없음")

# ── 집중도 경보 ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("⚠️ 거래처 집중도 경보")

col_c1, col_c2, col_c3 = st.columns(3)
col_c1.metric("총 거래처 수", f"{conc['총거래처수']}개")
col_c2.metric("총 외상매출금 잔액", fmt_krw(conc['총잔액']))

집중도 = conc["상위5집중도"]
if 집중도 >= 60:
    col_c3.metric("상위 5개사 집중도", f"{집중도:.1f}%", delta="매우 위험", delta_color="inverse")
elif 집중도 >= 50:
    col_c3.metric("상위 5개사 집중도", f"{집중도:.1f}%", delta="주의", delta_color="inverse")
else:
    col_c3.metric("상위 5개사 집중도", f"{집중도:.1f}%")

if conc["상위5"]:
    top5_raw = pd.DataFrame(conc["상위5"])
    top5_raw["비중(%)"] = (top5_raw["잔액"] / conc["총잔액"] * 100).round(1)
    st.dataframe(
        top5_raw, use_container_width=True, hide_index=True,
        column_config={"잔액": st.column_config.NumberColumn("잔액", format="localized")},
    )

# ── 연령분석 도넛 ────────────────────────────────────────────────────────
st.markdown("---")
col_chart, col_ratio = st.columns([2, 1])

aging_summary = {
    "정상(0-30)": aging_df["정상(0-30)"].sum(),
    "주의(31-60)": aging_df["주의(31-60)"].sum(),
    "경고(61-90)": aging_df["경고(61-90)"].sum(),
    "악성(91+)":   aging_df["악성(91+)"].sum(),
}

with col_chart:
    st.subheader("연령 구간별 비중")
    fig_pie = go.Figure(go.Pie(
        labels=list(aging_summary.keys()),
        values=list(aging_summary.values()),
        hole=0.45,
        marker_colors=["#4caf50", "#ffeb3b", "#ff9800", "#f44336"],
        textinfo="label+percent",
    ))
    fig_pie.update_layout(
        height=280, margin=dict(t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig_pie, use_container_width=True)

with col_ratio:
    st.subheader("구간별 금액")
    icons = {"정상(0-30)": "🟢", "주의(31-60)": "🟡", "경고(61-90)": "🟠", "악성(91+)": "🔴"}
    for label, amount in aging_summary.items():
        st.markdown(f"{icons[label]} **{label}**: {fmt_krw(amount)}")

# ── 거래처별 현황 (경과일 정렬) ──────────────────────────────────────────
st.markdown("---")
st.subheader("📊 거래처별 현황 (마지막 입금 경과일 순)")
st.caption("⚠️ = 정정전표 있는 거래처 — 잔액·회수율은 참고용 | ERP 대조 후 확정")

if not aging_df.empty:
    sort_df = aging_df.copy()
    if "경과일" in sort_df.columns:
        sort_df = sort_df.sort_values("경과일", ascending=False, na_position="last")

    display_aging = sort_df.copy()
    if "마지막입금일" in display_aging.columns:
        display_aging["마지막입금일"] = display_aging["마지막입금일"].apply(
            lambda v: str(v) if pd.notna(v) else "—"
        )
    if "정정전표" in display_aging.columns:
        display_aging["정정전표"] = display_aging["정정전표"].apply(lambda v: "⚠️" if v else "")

    show_cols = [c for c in ["거래처", "잔액", "악성(91+)", "경과일", "마지막입금일", "정정전표",
                              "주의(31-60)", "경고(61-90)", "정상(0-30)"] if c in display_aging.columns]
    money_cols = ["잔액", "정상(0-30)", "주의(31-60)", "경고(61-90)", "악성(91+)"]
    st.dataframe(
        display_aging[show_cols], use_container_width=True, hide_index=True, height=400,
        column_config={
            **{c: st.column_config.NumberColumn(c, format="localized") for c in money_cols if c in show_cols},
            "경과일": st.column_config.NumberColumn("경과일", format="%d일"),
        },
    )

# ── 거래처 심층 분석 ─────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🔍 거래처 심층 분석")
st.caption("거래처를 선택하면 발생/회수 추이·완결DSO·미수 경과일을 한 화면에서 분석합니다.")

partners_list = sorted(aging_df["거래처"].dropna().tolist()) if not aging_df.empty else []
sel_partner = st.selectbox(
    "거래처 검색·선택",
    ["선택하세요"] + partners_list,
    key="partner_drill",
)

if sel_partner != "선택하세요":
    with st.spinner(f"{sel_partner} 분석 중..."):
        deep = calc_partner_deep(df, sel_partner, as_of=as_of)

    # ── 요약 카드 ──────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("누계 발생액", fmt_krw(deep["total_dr"]))
    c2.metric("누계 회수액", fmt_krw(deep["total_cr"]))
    bal = deep["balance"]
    c3.metric("현재 잔액(FIFO)", fmt_krw(bal),
              delta="ERP 일치" if abs(bal - deep["balance"]) < 1000 else None)

    c4, c5, c6, c7 = st.columns(4)
    회수율 = min(deep["total_cr"], deep["total_dr"]) / deep["total_dr"] * 100 if deep["total_dr"] > 0 else 0
    c4.metric("회수율", f"{회수율:.1f}%")
    c5.metric("완결 DSO",
              f"{deep['completed_dso']:.0f}일" if deep["completed_dso"] else "—",
              delta="회수된 채권 평균 소요일",
              delta_color="off")
    c6.metric("미수 가중 경과일",
              f"{deep['outstanding_weighted_age']:.0f}일" if deep["outstanding_weighted_age"] else "—",
              delta="⚠️ 위험" if deep["outstanding_weighted_age"] > 90 else "정상",
              delta_color="inverse" if deep["outstanding_weighted_age"] > 90 else "off")
    c7.metric("최장 미수",
              f"{deep['oldest_age']}일" if deep["oldest_age"] else "—",
              delta="🔴" if deep["oldest_age"] > 180 else None,
              delta_color="off")

    avg_bal = deep["avg_balance"]
    st.caption(
        f"📊 월평균 AR 잔액: **{fmt_krw(avg_bal)}** "
        f"{'| ⚠️ 정정전표 있음' if deep['has_correction'] else ''}"
    )

    # ── 월별 발생/회수/잔액 추이 차트 ─────────────────────────────────
    all_ym = sorted(set(list(deep["monthly_dr"].keys()) + list(deep["monthly_cr"].keys())))
    if all_ym:
        fig_deep = go.Figure()
        fig_deep.add_trace(go.Bar(
            name="발생", x=all_ym,
            y=[deep["monthly_dr"].get(ym, 0) / 1e6 for ym in all_ym],
            marker_color="#60a5fa", opacity=0.75,
        ))
        fig_deep.add_trace(go.Bar(
            name="회수", x=all_ym,
            y=[deep["monthly_cr"].get(ym, 0) / 1e6 for ym in all_ym],
            marker_color="#34d399", opacity=0.75,
        ))
        fig_deep.add_trace(go.Scatter(
            name="월말 잔액", x=all_ym,
            y=[deep["monthly_balance"].get(ym, 0) / 1e6 for ym in all_ym],
            mode="lines+markers",
            line=dict(color="#f87171", width=2),
            marker=dict(size=5),
            yaxis="y2",
        ))
        fig_deep.update_layout(
            barmode="group",
            yaxis=dict(title="발생/회수 (백만원)", tickformat=",.0f"),
            yaxis2=dict(title="잔액 (백만원)", overlaying="y", side="right",
                        tickformat=",.0f"),
            height=360, margin=dict(t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hovermode="x unified",
        )
        st.plotly_chart(fig_deep, use_container_width=True)
        st.caption("파란 막대=발생 | 초록 막대=회수 | 빨간 선=월말잔액 (우측 Y축)")

    # ── 결제 기간 분포 (이 거래처만) ──────────────────────────────────
    with st.spinner("결제 패턴 분석 중..."):
        p_pattern = calc_payment_pattern(df)
    partner_pat = p_pattern[p_pattern["거래처"] == sel_partner] if not p_pattern.empty else pd.DataFrame()

    col_donut, col_info = st.columns([1, 1])
    with col_donut:
        # 현재 미수 aging (FIFO 잔액 기준)
        partner_aging = aging_df[aging_df["거래처"] == sel_partner]
        if not partner_aging.empty:
            row_ag = partner_aging.iloc[0]
            aging_data = {
                "정상(0-30)": row_ag.get("정상(0-30)", 0),
                "주의(31-60)": row_ag.get("주의(31-60)", 0),
                "경고(61-90)": row_ag.get("경고(61-90)", 0),
                "악성(91+)": row_ag.get("악성(91+)", 0),
            }
            if sum(aging_data.values()) > 0:
                st.markdown("**현재 미수 구간**")
                fig_ag = go.Figure(go.Pie(
                    labels=list(aging_data.keys()),
                    values=list(aging_data.values()),
                    hole=0.45,
                    marker_colors=["#4caf50", "#ffeb3b", "#ff9800", "#f44336"],
                    textinfo="label+percent",
                ))
                fig_ag.update_layout(
                    height=240, margin=dict(t=5, b=5),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"), showlegend=False,
                )
                st.plotly_chart(fig_ag, use_container_width=True)

    with col_info:
        st.markdown("**과거 완결 결제패턴** (FIFO 기준)")
        if not partner_pat.empty:
            pr = partner_pat.iloc[0]
            st.markdown(f"- 평균 결제일: **{pr['평균결제일']}일**")
            st.markdown(f"- 권장 여신: **{pr['권장여신(일)']}일**")
            st.markdown(f"- 30일 이내: {pr['30일이내(%)']:.0f}%")
            st.markdown(f"- 31~60일: {pr['31-60일(%)']:.0f}%")
            st.markdown(f"- 61~90일: {pr['61-90일(%)']:.0f}%")
            st.markdown(f"- 91일+: {pr['91일이상(%)']:.0f}%")
        else:
            st.info("완결된 채권 이력 없음")
        st.markdown("")
        st.markdown("**완결 DSO vs 미수 경과일 해석**")
        c_dso = deep["completed_dso"]
        w_age = deep["outstanding_weighted_age"]
        if c_dso and w_age:
            gap = w_age - c_dso
            if gap > 60:
                st.error(f"🔴 미수가 완결보다 {gap:.0f}일 더 오래됨 — 최근 결제 중단 가능성")
            elif gap > 20:
                st.warning(f"🟡 미수 경과일({w_age:.0f}일)이 완결DSO({c_dso:.0f}일)보다 길어지는 추세")
            else:
                st.success(f"🟢 완결DSO {c_dso:.0f}일 | 미수 경과 {w_age:.0f}일 — 정상 범위")
else:
    st.info("거래처를 선택하면 발생/회수 추이와 DSO가 표시됩니다.")

# ── 110 받을어음 잔액 모니터링 ────────────────────────────────────────────
st.markdown("---")
st.subheader("📄 받을어음(110) 미현금화 현황")
st.caption("어음 수취 후 아직 보통예금으로 입금되지 않은 금액 — 부도 시 실질 손실")

bills_info = calc_bills_receivable(df)
total_bills = bills_info["미현금화잔액"]
bills_by_partner = bills_info["거래처별"]

col_b1, col_b2 = st.columns(2)
col_b1.metric("전체 미현금화 어음 잔액", fmt_krw(total_bills),
              delta="부도 위험 노출 금액" if total_bills > 0 else "없음",
              delta_color="inverse" if total_bills > 0 else "off")

if not bills_by_partner.empty:
    with col_b2:
        st.markdown("**거래처별 어음수취액 (미현금화 포함)**")
        st.dataframe(
            bills_by_partner, use_container_width=True, hide_index=True,
            column_config={"어음수취액": st.column_config.NumberColumn("어음수취액", format="localized")},
        )
    st.caption(f"💡 전체 어음 잔액 {fmt_krw(total_bills)} — 어음이 현금화되면 보통예금(103) 입금, 부도 시 대손처리 필요")
else:
    col_b2.info("어음 거래 없음")

# ── 악성 미수금 ─────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🚨 악성 미수금 거래처 (91일+ 즉시 연락)")

악성_df = aging_df[aging_df["악성(91+)"] > 0].sort_values("악성(91+)", ascending=False)
if 악성_df.empty:
    st.success("91일 초과 미수금 없음")
else:
    display = 악성_df[["거래처", "잔액", "악성(91+)"]].copy()
    display.columns = ["거래처", "전체 잔액", "91일+ 미수금"]
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "전체 잔액": st.column_config.NumberColumn("전체 잔액", format="localized"),
            "91일+ 미수금": st.column_config.NumberColumn("91일+ 미수금", format="localized"),
        },
    )
    st.warning(
        "💡 **대손상각 처리 방법**: 위하고에서 `차변: 대손상각비 / 대변: 외상매출금(108)` 분개 입력 후 "
        "데이터허브에서 해당 월 재업로드하면 자동 반영됩니다."
    )

# ── 전체 연령분석표 ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("전체 거래처 연령분석표")

search = st.text_input("거래처 검색", placeholder="거래처명 입력...")
filtered = aging_df.copy()
if search:
    filtered = filtered[filtered["거래처"].str.contains(search, na=False)]
if "경과일" in filtered.columns:
    filtered = filtered.sort_values("경과일", ascending=False, na_position="last")

display_aging2 = filtered.copy()
if "마지막입금일" in display_aging2.columns:
    display_aging2["마지막입금일"] = display_aging2["마지막입금일"].apply(lambda v: str(v) if pd.notna(v) else "—")
if "정정전표" in display_aging2.columns:
    display_aging2["정정전표"] = display_aging2["정정전표"].apply(lambda v: "⚠️" if v else "")

money_cols2 = ["잔액", "정상(0-30)", "주의(31-60)", "경고(61-90)", "악성(91+)"]
st.dataframe(
    display_aging2, use_container_width=True, hide_index=True, height=400,
    column_config={
        **{c: st.column_config.NumberColumn(c, format="localized") for c in money_cols2 if c in display_aging2.columns},
        "경과일": st.column_config.NumberColumn("경과일", format="%d일"),
    },
)

# ── 다음 달 채권 관리 포인트 ────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 다음 달 채권 관리 포인트")

actions = []
if not 악성_df.empty:
    top = 악성_df.iloc[0]
    actions.append(f"🚨 **{top['거래처']}** 등 악성 미수업체에 즉시 연락")
if 집중도 >= 50 and conc["상위5"]:
    top1 = conc["상위5"][0]["거래처"]
    actions.append(f"⚠️ **{top1}** 집중도 {집중도:.1f}% — 거래처 다변화 검토")
주의금액 = aging_df["주의(31-60)"].sum()
if 주의금액 > 5e7:
    actions.append(f"📞 주의 구간(31~60일) {fmt_krw(주의금액)} — 연체 전환 방지 연락")
if not summary_df.empty and "회수율(%)" in summary_df.columns:
    위험업체 = summary_df[summary_df["회수율(%)"] < 80]
    if not 위험업체.empty:
        업체명 = 위험업체.iloc[0]["거래처"]
        회수율 = 위험업체.iloc[0]["회수율(%)"]
        actions.append(f"⚠️ **{업체명}** 등 회수율 {회수율:.1f}% 미만 (ERP 대조 후 확정)")

if not actions:
    actions.append("✅ 특별한 긴급 항목 없음")
for a in actions:
    st.markdown(f"- {a}")
