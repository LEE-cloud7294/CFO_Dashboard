import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
import calendar
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal_upto, get_available_months, load_master_blacklist
from core.aging import (calc_aging, calc_ar_summary, calc_concentration,
                        calc_overdue_ratio, extract_ar_collections,
                        calc_payment_pattern, calc_bills_receivable)
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


@st.cache_data(ttl=3600)
def load_cumulative(ym: str):
    """창업일~선택월 말일 전체 누적 로드 — 날짜 제한 없음 (FIFO 정확도 우선)."""
    df = load_journal_upto(ym)
    if not df.empty and "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return df


@st.cache_data(ttl=3600)
def get_blacklist():
    return load_master_blacklist()


with st.spinner(f"{selected_ym} 말일 기준 전체 누적 데이터 불러오는 중 (최초 로딩 시 시간 소요)..."):
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
        blank_cr = ar_raw[(ar_raw["대변"]>0) & (ar_raw["거래처"].astype(str).str.strip()=="")]
        if len(blank_cr) > 0:
            st.warning(f"⚠️ 회수 행 중 거래처 공란: {len(blank_cr)}개 → 전표번호로 자동 보완 적용")

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
    top5_raw["잔액"] = top5_raw["잔액"].apply(fmt_krw)
    st.dataframe(top5_raw, use_container_width=True, hide_index=True)

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

# ── 거래처별 회수율 · DSO · 회수수단 ────────────────────────────────────
st.markdown("---")
st.subheader("📊 거래처별 회수율 · 평균회수일(DSO) · 회수수단")

# 회수수단 분석 (현금/어음수취/대손상각)
if not summary_df.empty:
    coll_df = extract_ar_collections(df)

    # 거래처별 회수수단 집계
    수단_pivot = pd.DataFrame()
    if not coll_df.empty:
        coll_grp = coll_df.groupby(["거래처", "수단"])["회수액"].sum().unstack(fill_value=0).reset_index()
        수단_pivot = coll_grp

    def recovery_badge(row):
        r = row["회수율(%)"]
        if r >= 95: return "🟢 양호"
        if r >= 80: return "🟡 주의"
        return "🔴 위험"

    # DSO 내림차순 (회수 느린 순)
    display_summary = summary_df.sort_values("DSO(일)", ascending=False, na_position="last").copy()
    display_summary["위험도"] = display_summary.apply(recovery_badge, axis=1)
    display_summary["발생액"] = display_summary["발생액"].apply(fmt_krw)
    display_summary["회수액"] = display_summary["회수액"].apply(fmt_krw)
    display_summary["잔액"] = display_summary["잔액"].apply(fmt_krw)
    display_summary["회수율(%)"] = display_summary["회수율(%)"].apply(lambda v: f"{v:.1f}%")
    display_summary["DSO(일)"] = display_summary["DSO(일)"].apply(
        lambda v: f"{v}일" if pd.notna(v) else "—"
    )

    # 어음수취 비중 추가
    if not 수단_pivot.empty and "어음수취" in 수단_pivot.columns:
        어음_map = 수단_pivot.set_index("거래처")["어음수취"].to_dict()
        display_summary["어음수취"] = display_summary["거래처"].map(
            lambda g: fmt_krw(어음_map.get(g, 0)) if 어음_map.get(g, 0) > 0 else "—"
        )
        show_cols = ["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)", "어음수취", "위험도"]
    else:
        show_cols = ["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)", "위험도"]

    st.dataframe(
        display_summary[show_cols],
        use_container_width=True, hide_index=True, height=380,
    )
    st.caption(
        "DSO = 발생일~현금회수일 FIFO 가중평균 | "
        "어음수취 = 현금화 전 받을어음 (미수취급 주의) | "
        "회수율·잔액은 창업일부터 선택월까지 전체 누적 기준"
    )
else:
    st.info("회수율 데이터 없음")

# ── 거래처별 실제 결제 기간 분석 (여신 관리) ─────────────────────────────
st.markdown("---")
st.subheader("📐 거래처별 실제 결제 기간 분석")
st.caption("FIFO 매칭으로 각 발생 건이 실제로 회수까지 얼마나 걸렸는지 추적 | '2달 여신'이 실제로 지켜지는지 확인")

with st.spinner("결제 패턴 분석 중..."):
    pattern_df = calc_payment_pattern(df)

if not pattern_df.empty:
    # 블랙리스트 필터
    if hide_blacklist:
        bl_set = set(get_blacklist())
        pattern_df = pattern_df[~pattern_df["거래처"].isin(bl_set)]

    # 요약 테이블
    display_pat = pattern_df.copy()
    display_pat["발생액"] = display_pat["발생액"].apply(fmt_krw)
    display_pat["잔액"] = display_pat["잔액"].apply(fmt_krw)
    display_pat["회수율(%)"] = display_pat["회수율(%)"].apply(lambda v: f"{v:.1f}%")
    display_pat["평균결제일"] = display_pat["평균결제일"].apply(lambda v: f"{v}일")
    display_pat["권장여신(일)"] = display_pat["권장여신(일)"].apply(lambda v: f"{v}일")

    # 결제 기간 분포 막대 (가로)
    st.markdown("**거래처별 결제기간 분포 (금액 기준 가중평균)**")

    fig_pat = go.Figure()
    colors = {"30일이내(%)": "#4ade80", "31-60일(%)": "#fbbf24",
              "61-90일(%)": "#fb923c", "91일이상(%)": "#f87171"}
    for col, color in colors.items():
        fig_pat.add_trace(go.Bar(
            name=col.replace("(%)", ""),
            x=pattern_df[col],
            y=pattern_df["거래처"],
            orientation="h",
            marker_color=color,
            text=pattern_df[col].apply(lambda v: f"{v:.0f}%" if v >= 5 else ""),
            textposition="inside",
            textfont_size=10,
        ))
    fig_pat.update_layout(
        barmode="stack",
        height=max(280, len(pattern_df) * 35),
        margin=dict(t=10, b=10, l=10, r=60),
        xaxis=dict(title="비중 (%)", range=[0, 100]),
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_pat, use_container_width=True)

    # 상세 테이블
    with st.expander("📋 거래처별 결제기간 상세 테이블"):
        show_cols = ["거래처", "발생액", "잔액", "회수율(%)",
                     "평균결제일", "최소결제일", "최대결제일",
                     "30일이내(%)", "31-60일(%)", "61-90일(%)", "91일이상(%)",
                     "권장여신(일)"]
        st.dataframe(display_pat[show_cols], use_container_width=True, hide_index=True)

    # 여신 초과 경보
    st.markdown("**⚠️ 권장 여신기간 vs 현황**")
    for _, row in pattern_df.iterrows():
        avg = row["평균결제일"]
        rec = row["권장여신(일)"]
        pct_over = row["91일이상(%)"]

        if pct_over >= 20:
            icon = "🔴"
            msg = f"91일 초과 비중 {pct_over:.0f}% — 여신 조건 재협의 필요"
        elif avg > 60:
            icon = "🟡"
            msg = f"평균 {avg}일 결제 — 여신 기준 재확인 필요"
        else:
            icon = "🟢"
            msg = f"평균 {avg}일 정상"

        st.markdown(
            f"{icon} **{row['거래처']}** — 평균 {avg}일, 권장여신 {rec}일 | {msg}"
        )
else:
    st.info("결제 패턴 데이터가 없습니다.")

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
        bills_display = bills_by_partner.copy()
        bills_display["어음수취액"] = bills_display["어음수취액"].apply(fmt_krw)
        st.dataframe(bills_display, use_container_width=True, hide_index=True)
    st.caption(
        f"💡 전체 어음 잔액 {fmt_krw(total_bills)} — "
        "어음이 현금화되면 보통예금(103) 입금, 부도 시 대손처리 필요"
    )
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
    display["잔액"] = display["잔액"].apply(fmt_krw)
    display["악성(91+)"] = display["악성(91+)"].apply(fmt_krw)
    display.columns = ["거래처", "전체 잔액", "91일+ 미수금"]
    st.dataframe(display, use_container_width=True, hide_index=True)
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

display_aging = filtered.copy()
for col in ["잔액", "정상(0-30)", "주의(31-60)", "경고(61-90)", "악성(91+)"]:
    display_aging[col] = display_aging[col].apply(fmt_krw)

st.dataframe(display_aging, use_container_width=True, hide_index=True, height=400)

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

if not summary_df.empty:
    위험업체 = summary_df[summary_df["회수율(%)"] < 80]
    if not 위험업체.empty:
        업체명 = 위험업체.iloc[0]["거래처"]
        회수율 = 위험업체.iloc[0]["회수율(%)"]
        actions.append(f"⚠️ **{업체명}** 등 회수율 {회수율:.1f}% 미만 — 결제 조건 재검토")

if not actions:
    actions.append("✅ 특별한 긴급 항목 없음")
for a in actions:
    st.markdown(f"- {a}")
