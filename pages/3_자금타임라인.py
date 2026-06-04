import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months, load_debts, load_all_journal

st.set_page_config(page_title="자금 타임라인", page_icon="💳", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("💳 자금 타임라인")
st.caption("대출 만기 경보 · 이자 이체 일정 · 월별 자금 흐름을 한눈에 파악합니다.")

today = date.today()

# ─── 데이터 로드 ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_debts_data():
    return load_debts()


@st.cache_data(ttl=300)
def load_interest_history():
    """931(이자비용) 계정 기준 월별 이자 합계."""
    df = load_all_journal()
    if df.empty:
        return pd.DataFrame()
    interest = df[df["계정코드"].astype(str) == "931"][["전표일자", "차변"]].copy()
    if interest.empty:
        return pd.DataFrame()
    interest["ym"] = interest["전표일자"].dt.strftime("%Y-%m")
    return interest.groupby("ym")["차변"].sum().reset_index()


@st.cache_data(ttl=300)
def load_cash_balance(ym: str) -> float:
    """선택 월의 103(보통예금) 잔액: 차변 합계 - 대변 합계."""
    df = load_journal(ym)
    if df.empty:
        return 0.0
    cash = df[df["계정코드"].astype(str) == "103"]
    return float(cash["차변"].sum() - cash["대변"].sum())


debts_df = load_debts_data()
interest_hist = load_interest_history()
months = get_available_months()

# ─── 상단 KPI 카드 ────────────────────────────────────────────────────────────
total_principal = debts_df["원금잔액"].fillna(0).sum() if not debts_df.empty else 0
total_monthly = debts_df["월상환액"].fillna(0).sum() if not debts_df.empty else 0

# 만기 임박 건수 (90일 이내)
alert_count = 0
if not debts_df.empty and "만기일" in debts_df.columns:
    debts_df["만기일_dt"] = pd.to_datetime(debts_df["만기일"], errors="coerce")
    alert_count = int(
        debts_df["만기일_dt"].dropna().apply(lambda d: (d.date() - today).days <= 90).sum()
    )

# 이자비용 최근월
latest_interest = 0
if not interest_hist.empty:
    latest_interest = float(interest_hist.iloc[-1]["차변"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("총 대출 잔액", f"{total_principal / 1e8:.1f}억 원")
c2.metric("월 이자·상환 합계", f"{total_monthly / 10000:,.0f}만 원")
c3.metric("분개장 이자비용 (최근월)", f"{latest_interest / 10000:,.0f}만 원")
c4.metric(
    "만기 임박 (90일 이내)",
    f"{alert_count}건",
    delta="주의 필요" if alert_count > 0 else "안전",
    delta_color="inverse" if alert_count > 0 else "off",
)

st.markdown("---")

# ─── 섹션 1: 만기 경보 ──────────────────────────────────────────────────────
st.header("🚨 대출 만기 경보")

if debts_df.empty:
    st.info("데이터 허브 → 대출 정보 탭에서 대출 내역을 입력해 주세요.")
elif "만기일" not in debts_df.columns:
    st.warning("만기일 컬럼이 없습니다.")
else:
    df_mat = debts_df[debts_df["만기일_dt"].notna()].copy()
    df_mat["D_day"] = df_mat["만기일_dt"].apply(lambda d: (d.date() - today).days)
    df_mat = df_mat.sort_values("D_day")

    has_alert = False
    for _, row in df_mat.iterrows():
        d = int(row["D_day"])
        bank = row.get("은행명", "")
        kind = row.get("대출종류", "")
        amt = row.get("원금잔액", 0) or 0
        mat = row["만기일_dt"].strftime("%Y-%m-%d")
        note = row.get("비고", "") or ""

        label = f"**{bank} {kind}** — {amt / 1e8:.1f}억 — 만기 {mat} (D{d:+d}) {note}"

        if d < 0:
            st.error(f"🚨 만기 초과! {label}")
            has_alert = True
        elif d <= 30:
            st.error(f"🔴 30일 이내 {label}")
            has_alert = True
        elif d <= 90:
            st.warning(f"🟡 90일 이내 {label}")
            has_alert = True

    if not has_alert:
        st.success("✅ 90일 이내 만기 도래 대출 없음")

st.markdown("---")

# ─── 섹션 2: 이자 이체 일정 ──────────────────────────────────────────────────
st.header("📅 이자·상환 이체 일정")

sel_ym = st.selectbox("기준 월", months, index=0) if months else None

if not debts_df.empty and "다음상환일" in debts_df.columns and sel_ym:
    sel_year = int(sel_ym[:4])
    sel_month = int(sel_ym[5:7])

    # 다음상환일에서 '일(day)'만 추출
    debts_df["상환일_dt"] = pd.to_datetime(debts_df["다음상환일"], errors="coerce")
    debts_df["이체일"] = debts_df["상환일_dt"].apply(
        lambda d: d.day if pd.notna(d) else None
    )

    schedule = debts_df[debts_df["이체일"].notna()].sort_values("이체일")

    if schedule.empty:
        st.info("등록된 이체 일정이 없습니다.")
    else:
        st.markdown(f"#### {sel_year}년 {sel_month}월 이체 일정")
        cols = st.columns(min(len(schedule), 4))
        for i, (_, row) in enumerate(schedule.iterrows()):
            with cols[i % min(len(schedule), 4)]:
                day = int(row["이체일"])
                bank = row.get("은행명", "?")
                amt = row.get("월상환액", 0) or 0
                try:
                    pay_date = date(sel_year, sel_month, day)
                    diff = (pay_date - today).days
                    dday = f"D{diff:+d}" if diff != 0 else "D-day"
                    badge = "🔴" if 0 <= diff <= 3 else ("🟡" if 0 <= diff <= 7 else "🟢")
                except ValueError:
                    dday = ""
                    badge = ""
                st.metric(
                    label=f"{bank} — {day}일 {badge}",
                    value=f"{amt / 10000:,.0f}만원",
                    delta=dday if dday else None,
                    delta_color="off",
                )

    # 이번 달 현금 현황
    cash = load_cash_balance(sel_ym)
    total_outflow = schedule["월상환액"].fillna(0).sum()
    st.markdown("---")
    col_cash1, col_cash2, col_cash3 = st.columns(3)
    col_cash1.metric("이번 달 보통예금 (103계정)", f"{cash / 1e8:.2f}억 원" if abs(cash) >= 1e7 else f"{cash / 10000:,.0f}만 원")
    col_cash2.metric("이번 달 이자·상환 합계", f"{total_outflow / 10000:,.0f}만 원")
    remaining = cash - total_outflow
    col_cash3.metric(
        "지급 후 예상 잔액",
        f"{remaining / 1e8:.2f}억 원" if abs(remaining) >= 1e7 else f"{remaining / 10000:,.0f}만 원",
        delta="⚠️ 자금 부족 주의" if remaining < 5e7 else "✅ 여유",
        delta_color="inverse" if remaining < 5e7 else "off",
    )
else:
    st.info("대출 정보가 없거나 분개장이 업로드되지 않았습니다.")

st.markdown("---")

# ─── 섹션 3: 이자비용 월 추이 ────────────────────────────────────────────────
st.header("📈 이자비용 월 추이 (분개장 931계정)")

if interest_hist.empty:
    st.info("분개장 데이터가 없습니다.")
else:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=interest_hist["ym"],
        y=interest_hist["차변"] / 10000,
        name="이자비용",
        marker_color="#ef4444",
        text=(interest_hist["차변"] / 10000).apply(lambda v: f"{v:,.0f}"),
        textposition="outside",
    ))
    fig.update_layout(
        xaxis_title="월",
        yaxis_title="이자비용 (만원)",
        height=320,
        margin=dict(t=30, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ─── 섹션 4: 대구 신규공장 시뮬레이터 ──────────────────────────────────────
st.header("🏗️ 대구 신규공장 투자 시뮬레이터")
st.caption("11억 설비투자 시 월간 자금 부담 시뮬레이션 (대출 구조 조정 포함)")

with st.expander("시뮬레이터 열기", expanded=False):
    col_in1, col_in2, col_res = st.columns([1, 1, 1])

    with col_in1:
        invest_amt = st.number_input("설비투자 금액 (억)", value=11.0, min_value=0.0, step=0.5, key="sim_invest")
        loan_ratio = st.slider("대출 비율 (%)", 0, 100, 70, key="sim_ratio")
        loan_rate = st.number_input("대출 금리 (%)", value=3.5, min_value=0.0, max_value=20.0, step=0.1, key="sim_rate")

    with col_in2:
        loan_years = st.number_input("대출 기간 (년)", value=5, min_value=1, max_value=20, key="sim_years")
        repay_type = st.radio("상환 방식", ["만기 일시상환", "원금균등분할"], key="sim_repay")

    loan_amt = invest_amt * loan_ratio / 100
    self_amt = invest_amt - loan_amt
    monthly_interest_new = loan_amt * 1e8 * (loan_rate / 100) / 12

    if repay_type == "원금균등분할":
        monthly_principal = loan_amt * 1e8 / (loan_years * 12)
        monthly_total_new = monthly_interest_new + monthly_principal
    else:
        monthly_principal = 0
        monthly_total_new = monthly_interest_new

    current_monthly = total_monthly  # 기존 월상환액 합계

    with col_res:
        st.markdown("#### 시뮬레이션 결과")
        st.metric("신규 대출액", f"{loan_amt:.1f}억")
        st.metric("자체 자금 필요", f"{self_amt:.1f}억")
        st.metric("추가 월 부담 (이자+원금)", f"{monthly_total_new / 10000:,.0f}만원")
        st.metric(
            "총 월 부담 (기존+신규)",
            f"{(current_monthly + monthly_total_new) / 10000:,.0f}만원",
            delta=f"+{monthly_total_new / 10000:,.0f}만원",
            delta_color="inverse",
        )

    st.markdown(f"""
    | 항목 | 금액 |
    |---|---|
    | 연간 이자 부담 | {monthly_interest_new * 12 / 10000:,.0f}만원 |
    | {loan_years}년 총 이자 | {monthly_interest_new * 12 * loan_years / 10000:,.0f}만원 |
    | 총 대출 잔액 (기존+신규) | {(total_principal + loan_amt * 1e8) / 1e8:.1f}억원 |
    """)

st.markdown("---")

# ─── 섹션 5: 대출 현황 전체 ──────────────────────────────────────────────────
st.header("🏦 대출 현황 전체")

if debts_df.empty:
    st.info("데이터 허브 → 대출 정보 탭에서 대출 내역을 입력해 주세요.")
else:
    display_cols = [c for c in
                    ["은행명", "대출종류", "원금잔액", "금리", "만기일", "다음상환일", "월상환액", "비고"]
                    if c in debts_df.columns]
    display_df = debts_df[display_cols].copy()

    if "원금잔액" in display_df.columns:
        display_df["원금잔액"] = display_df["원금잔액"].apply(
            lambda v: f"{v / 1e8:.1f}억" if pd.notna(v) and v > 0 else "-"
        )
    if "금리" in display_df.columns:
        display_df["금리"] = display_df["금리"].apply(
            lambda v: f"{v:.2f}%" if pd.notna(v) and v > 0 else "-"
        )
    if "월상환액" in display_df.columns:
        display_df["월상환액"] = display_df["월상환액"].apply(
            lambda v: f"{v / 10000:,.0f}만원" if pd.notna(v) and v > 0 else "-"
        )

    def row_style(row):
        idx = row.name
        try:
            d = debts_df.loc[idx, "만기일_dt"]
            if pd.isna(d):
                return [""] * len(row)
            days = (d.date() - today).days
            if days < 0:
                return ["background-color: #fee2e2"] * len(row)
            if days <= 90:
                return ["background-color: #fef9c3"] * len(row)
        except Exception:
            pass
        return [""] * len(row)

    st.dataframe(display_df.style.apply(row_style, axis=1), use_container_width=True, hide_index=True)

    col_t1, col_t2 = st.columns(2)
    col_t1.metric("총 대출 잔액", f"{total_principal / 1e8:.1f}억 원")
    col_t2.metric("월 이자·상환 합계", f"{total_monthly / 10000:,.0f}만 원")

st.markdown("---")
st.caption("💡 **다음달 뭘 할까** — 만기 도래 대출 연장 협의, 이자 이체일 전 자금 여유 확보, 대구 신규투자 대출 구조 검토")
