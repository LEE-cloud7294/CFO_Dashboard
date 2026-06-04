import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, load_journal_code, get_available_months, load_debts

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
st.caption("통장 실제 흐름 — '다음 달 자금이 버텨지나' 판단")

today = date.today()

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_debts_data():
    return load_debts()


@st.cache_data(ttl=3600)
def load_interest_history():
    """931(이자비용) 계정 기준 월별 합계 — load_journal_code 사용으로 httpx 에러 방지."""
    df = load_journal_code("931")
    if df.empty:
        return pd.DataFrame()
    df["ym"] = df["전표일자"].dt.strftime("%Y-%m")
    return df.groupby("ym")["차변"].sum().reset_index()


@st.cache_data(ttl=3600)
def load_capex_history():
    """설비투자 이력 — Code 206(기계장치)·208(차량)·240(소프트웨어) 차변."""
    frames = []
    for code in ["206", "208", "240"]:
        df = load_journal_code(code)
        if not df.empty:
            df["계정코드"] = code
            frames.append(df[df["차변"] > 0])
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["ym"] = combined["전표일자"].dt.strftime("%Y-%m")
    return combined


@st.cache_data(ttl=300)
def load_cash_balance(ym: str) -> float:
    """선택 월의 103(보통예금) 순잔액."""
    df = load_journal(ym)
    if df.empty:
        return 0.0
    cash = df[df["계정코드"].astype(str) == "103"]
    return float(cash["차변"].sum() - cash["대변"].sum())


debts_df = load_debts_data()
interest_hist = load_interest_history()
capex_hist = load_capex_history()
months = get_available_months()

# ── 상단 KPI ─────────────────────────────────────────────────────────────────
total_principal = debts_df["원금잔액"].fillna(0).sum() if not debts_df.empty else 0
total_monthly = debts_df["월상환액"].fillna(0).sum() if not debts_df.empty else 0

alert_count = 0
if not debts_df.empty and "만기일" in debts_df.columns:
    debts_df["만기일_dt"] = pd.to_datetime(debts_df["만기일"], errors="coerce")
    alert_count = int(
        debts_df["만기일_dt"].dropna().apply(lambda d: (d.date() - today).days <= 90).sum()
    )

latest_interest = float(interest_hist.iloc[-1]["차변"]) if not interest_hist.empty else 0

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

# ── 섹션 1: 만기 경보 ────────────────────────────────────────────────────────
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

# ── 섹션 2: 이자 이체 일정 ───────────────────────────────────────────────────
st.header("📅 이자·상환 이체 일정")

sel_ym = st.selectbox("기준 월", months, index=0) if months else None

if not debts_df.empty and "다음상환일" in debts_df.columns and sel_ym:
    sel_year = int(sel_ym[:4])
    sel_month = int(sel_ym[5:7])

    debts_df["상환일_dt"] = pd.to_datetime(debts_df["다음상환일"], errors="coerce")
    debts_df["이체일"] = debts_df["상환일_dt"].apply(lambda d: d.day if pd.notna(d) else None)
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
                    dday, badge = "", ""
                st.metric(
                    label=f"{bank} — {day}일 {badge}",
                    value=f"{amt / 10000:,.0f}만원",
                    delta=dday if dday else None,
                    delta_color="off",
                )

    # 이번 달 자금 여유 판단
    cash = load_cash_balance(sel_ym)
    total_outflow = schedule["월상환액"].fillna(0).sum()
    st.markdown("---")
    col_cash1, col_cash2, col_cash3 = st.columns(3)
    col_cash1.metric("이번 달 보통예금 (103계정)",
                     f"{cash / 1e8:.2f}억 원" if abs(cash) >= 1e7 else f"{cash / 10000:,.0f}만 원")
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

# ── 섹션 2-5: 보통예금 실제 현금 흐름 ────────────────────────────────────────
st.header("💰 보통예금(103) 실제 현금 흐름")

if sel_ym:
    @st.cache_data(ttl=300)
    def load_cashflow(ym: str):
        df = load_journal(ym)
        if df.empty:
            return pd.DataFrame()
        return df[df["계정코드"].astype(str) == "103"].copy()

    cf = load_cashflow(sel_ym)

    if cf.empty:
        st.info("해당 월 보통예금(103) 데이터가 없습니다.")
    else:
        total_in = cf["차변"].sum()    # 입금
        total_out = cf["대변"].sum()   # 출금
        net = total_in - total_out

        ci1, ci2, ci3 = st.columns(3)
        ci1.metric("총 입금", f"{total_in/1e8:.2f}억원")
        ci2.metric("총 출금", f"{total_out/1e8:.2f}억원")
        ci3.metric(
            "순 현금 변화",
            f"{net/1e8:+.2f}억원",
            delta="입금 우세" if net > 0 else "출금 우세",
            delta_color="normal" if net > 0 else "inverse",
        )

        col_in, col_out = st.columns(2)

        with col_in:
            st.subheader("📥 주요 입금")
            inflow = cf[cf["차변"] > 0].copy()
            if not inflow.empty:
                top_in = (
                    inflow.groupby("거래처")["차변"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(8)
                    .reset_index()
                )
                top_in.columns = ["거래처", "금액"]
                top_in["비중"] = (top_in["금액"] / total_in * 100).apply(lambda v: f"{v:.1f}%")
                top_in["금액"] = top_in["금액"].apply(lambda v: f"{v/1e6:.1f}백만")
                st.dataframe(top_in, use_container_width=True, hide_index=True)

        with col_out:
            st.subheader("📤 주요 출금")
            outflow = cf[cf["대변"] > 0].copy()
            if not outflow.empty:
                top_out = (
                    outflow.groupby("거래처")["대변"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(8)
                    .reset_index()
                )
                top_out.columns = ["거래처", "금액"]
                top_out["비중"] = (top_out["금액"] / total_out * 100).apply(lambda v: f"{v:.1f}%")
                top_out["금액"] = top_out["금액"].apply(lambda v: f"{v/1e6:.1f}백만")
                st.dataframe(top_out, use_container_width=True, hide_index=True)

st.markdown("---")

# ── 섹션 3: 이자비용 월 추이 ──────────────────────────────────────────────────
st.header("📈 이자비용 월 추이 (분개장 931계정)")

if interest_hist.empty:
    st.info("분개장 데이터가 없습니다.")
else:
    fig = go.Figure(go.Bar(
        x=interest_hist["ym"],
        y=interest_hist["차변"] / 10000,
        name="이자비용",
        marker_color="#ef4444",
        text=(interest_hist["차변"] / 10000).apply(lambda v: f"{v:,.0f}"),
        textposition="outside",
    ))
    fig.update_layout(
        xaxis_title="월", yaxis_title="이자비용 (만원)",
        height=320, margin=dict(t=30, b=40),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ── 섹션 4: 설비투자 이력 ────────────────────────────────────────────────────
st.header("🏗️ 설비투자 이력 (기계장치·차량·소프트웨어)")

if capex_hist.empty:
    st.info("설비투자(Code 206/208/240) 데이터가 없습니다.")
else:
    code_label = {"206": "기계장치", "208": "차량운반구", "240": "소프트웨어"}
    capex_by_ym = capex_hist.groupby(["ym", "계정코드"])["차변"].sum().reset_index()

    fig2 = go.Figure()
    colors = {"206": "#60a5fa", "208": "#34d399", "240": "#fbbf24"}
    for code, label in code_label.items():
        sub = capex_by_ym[capex_by_ym["계정코드"] == code]
        if not sub.empty:
            fig2.add_trace(go.Bar(
                x=sub["ym"], y=sub["차변"] / 1e6,
                name=label, marker_color=colors[code],
            ))
    fig2.update_layout(
        barmode="stack",
        xaxis_title="월", yaxis_title="설비투자 (백만원)",
        height=300, margin=dict(t=20, b=40),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig2, use_container_width=True)

    # 최근 투자 상세
    recent = capex_hist.sort_values("전표일자", ascending=False).head(10)
    recent["금액"] = recent["차변"].apply(lambda v: f"{v/1e6:.1f}백만")
    recent["구분"] = recent["계정코드"].map(code_label)
    st.dataframe(
        recent[["전표일자", "구분", "금액", "거래처", "적요"]].reset_index(drop=True),
        use_container_width=True, hide_index=True,
    )

st.markdown("---")

# ── 섹션 5: 대구 신규공장 시뮬레이터 ────────────────────────────────────────
st.header("🏗️ 설비투자 자금 부담 시뮬레이터")

with st.expander("시뮬레이터 열기", expanded=False):
    col_in1, col_in2, col_res = st.columns([1, 1, 1])

    with col_in1:
        invest_amt = st.number_input("설비투자 금액 (억)", value=11.0, min_value=0.0, step=0.5)
        loan_ratio = st.slider("대출 비율 (%)", 0, 100, 70)
        loan_rate = st.number_input("대출 금리 (%)", value=3.5, min_value=0.0, max_value=20.0, step=0.1)

    with col_in2:
        loan_years = st.number_input("대출 기간 (년)", value=5, min_value=1, max_value=20)
        repay_type = st.radio("상환 방식", ["만기 일시상환", "원금균등분할"])

    loan_amt = invest_amt * loan_ratio / 100
    self_amt = invest_amt - loan_amt
    monthly_interest_new = loan_amt * 1e8 * (loan_rate / 100) / 12
    if repay_type == "원금균등분할":
        monthly_principal = loan_amt * 1e8 / (loan_years * 12)
        monthly_total_new = monthly_interest_new + monthly_principal
    else:
        monthly_principal = 0
        monthly_total_new = monthly_interest_new

    with col_res:
        st.markdown("#### 시뮬레이션 결과")
        st.metric("신규 대출액", f"{loan_amt:.1f}억")
        st.metric("자체 자금 필요", f"{self_amt:.1f}억")
        st.metric("추가 월 부담", f"{monthly_total_new / 10000:,.0f}만원")
        st.metric(
            "총 월 부담 (기존+신규)",
            f"{(total_monthly + monthly_total_new) / 10000:,.0f}만원",
            delta=f"+{monthly_total_new / 10000:,.0f}만원",
            delta_color="inverse",
        )

st.markdown("---")

# ── 섹션 6: 대출 현황 전체 ───────────────────────────────────────────────────
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
        try:
            d = debts_df.loc[row.name, "만기일_dt"]
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
st.caption("💡 **다음달 챙길 일** — 만기 도래 대출 연장 협의, 이자 이체일 전 자금 여유 확보")
