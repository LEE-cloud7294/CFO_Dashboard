import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, load_journal_code, get_available_months, load_debts
from core.metrics import fmt_krw

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

# ── 원재료·부재료 업체 목록 (CLAUDE.md §12) ──────────────────────────────────
RAW_VENDORS = ["KCC글라스", "KCC", "LX글라스", "엘엑스", "한유에스앤지", "한성유엔씨"]
SUB_VENDORS = ["굳센글로벌", "한국마그네슘", "과림에프씨"]


# ── 데이터 로드 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_debts_data():
    return load_debts()


@st.cache_data(ttl=3600)
def load_interest_history():
    """931+103 실제 이체 기준 이자비용 월별 추이 — load_journal_code로 속도 개선."""
    df931 = load_journal_code("931")
    df103 = load_journal_code("103")
    if df931.empty or df103.empty:
        return pd.DataFrame()

    df931["ym"] = df931["전표일자"].dt.strftime("%Y-%m")
    df103["ym"] = df103["전표일자"].dt.strftime("%Y-%m")

    # 931+103 같은 전표번호 → 이자 이체 금액
    if "전표번호" not in df931.columns or "전표번호" not in df103.columns:
        # 전표번호 없으면 931 차변 그대로
        return df931.groupby("ym")["차변"].sum().reset_index()

    vnos_with_931 = set(df931[df931["차변"] > 0]["전표번호"])
    matched103 = df103[
        (df103["전표번호"].isin(vnos_with_931)) & (df103["대변"] > 0)
    ]
    if matched103.empty:
        return df931.groupby("ym")["차변"].sum().reset_index()
    return matched103.groupby("ym")["대변"].sum().reset_index().rename(columns={"대변": "차변"})


@st.cache_data(ttl=3600)
def load_capex_history():
    """설비투자 이력 — Code 206·208·240 차변."""
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
def load_cashflow_detail(ym: str) -> pd.DataFrame:
    """보통예금(103) 월별 입출금 전체 — 계정코드 기반 분류."""
    df = load_journal(ym)
    if df.empty:
        return pd.DataFrame()
    cf = df[df["계정코드"].astype(str) == "103"].copy()
    paired = df.groupby("전표번호")["계정코드"].apply(lambda x: x.astype(str).tolist()).to_dict()

    def classify_in(row):
        codes = paired.get(row["전표번호"], [])
        적요 = str(row.get("적요", ""))
        if "108" in codes:
            return "매출채권 수금"
        if "110" in codes or "추심" in 적요:
            return "어음 추심"
        return "기타 입금"

    def classify_out(row):
        codes = paired.get(row["전표번호"], [])
        거래처 = str(row.get("거래처", ""))
        적요 = str(row.get("적요", ""))
        # 계정코드 기반 (우선)
        if "931" in codes:
            return "이자 이체"
        if any(c in codes for c in ["206", "208", "240"]):
            return "설비투자"
        if "255" in codes:
            return "부가세 납부"
        if "251" in codes:
            return "원재료 결제"
        if "253" in codes:
            return "미지급금 결제"
        if "162" in codes:
            return "부재료 결제"
        if "153" in codes:
            return "원재료 결제"
        # 급여 계정 (5xx/8xx 급여계정 범위)
        if any(c in codes for c in ["510", "511", "512", "513", "810", "811", "812"]):
            return "인건비 이체"
        # 거래처명 기반 (보조)
        if any(v in 거래처 for v in RAW_VENDORS):
            return "원재료 결제"
        if any(v in 거래처 for v in SUB_VENDORS):
            return "부재료 결제"
        # 적요 기반 (최후 수단)
        if "급여" in 적요 or "임금" in 적요:
            return "인건비 이체"
        if "이자" in 적요:
            return "이자 이체"
        if "부가세" in 적요 or "세금" in 적요:
            return "부가세 납부"
        if "대출" in 적요 or "상환" in 적요:
            return "대출 상환"
        return "기타 지출"

    cf = cf.copy()
    cf["유형"] = ""
    mask_in = cf["차변"] > 0
    mask_out = cf["대변"] > 0
    if mask_in.any():
        cf.loc[mask_in, "유형"] = cf[mask_in].apply(classify_in, axis=1)
    if mask_out.any():
        cf.loc[mask_out, "유형"] = cf[mask_out].apply(classify_out, axis=1)
    return cf


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
c1.metric("총 대출 잔액", fmt_krw(total_principal))
c2.metric("월 이자·상환 합계", fmt_krw(total_monthly))
c3.metric("이자비용 (최근월)", fmt_krw(latest_interest))
c4.metric(
    "만기 임박 (90일 이내)", f"{alert_count}건",
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
        label = f"**{bank} {kind}** — {fmt_krw(amt)} — 만기 {mat} (D{d:+d}) {note}"
        if d < 0:
            st.error(f"🚨 만기 초과! {label}"); has_alert = True
        elif d <= 30:
            st.error(f"🔴 30일 이내 {label}"); has_alert = True
        elif d <= 90:
            st.warning(f"🟡 90일 이내 {label}"); has_alert = True
    if not has_alert:
        st.success("✅ 90일 이내 만기 도래 대출 없음")

st.markdown("---")

# ── 섹션 2: 보통예금 현금흐름 (월별 / 연간 탭) ────────────────────────────────
st.header("💰 보통예금(103) 현금 흐름")

tab_monthly, tab_annual = st.tabs(["📅 월별 상세", "📊 연간 요약"])

# ── 월별 탭 ──────────────────────────────────────────────────────────────────
with tab_monthly:
    sel_ym = st.selectbox("기준 월", months, index=0, key="cf_month") if months else None

    if sel_ym:
        cf = load_cashflow_detail(sel_ym)

        if cf.empty:
            st.info("해당 월 보통예금(103) 데이터가 없습니다.")
        else:
            total_in  = cf[cf["차변"] > 0]["차변"].sum()
            total_out = cf[cf["대변"] > 0]["대변"].sum()
            net = total_in - total_out

            in_df  = cf[cf["차변"] > 0].groupby("유형")["차변"].sum().sort_values(ascending=False)
            out_df = cf[cf["대변"] > 0].groupby("유형")["대변"].sum().sort_values(ascending=False)

            rows_html = ""

            # 입금 섹션
            rows_html += "<tr style='background:#1e3a5f;'><td colspan='3' style='font-weight:bold;color:#60a5fa;padding:6px 12px;'>📥 입금</td></tr>"
            for cat, amt in in_df.items():
                pct = f"{amt/total_in*100:.1f}%" if total_in > 0 else "—"
                rows_html += f"<tr><td style='padding:4px 12px;'>&nbsp;&nbsp;{cat}</td><td style='text-align:right;color:#4ade80;'>+{fmt_krw(amt)}</td><td style='text-align:right;color:#9ca3af;'>{pct}</td></tr>"
            rows_html += f"<tr style='border-top:2px solid #374151;'><td style='padding:6px 12px;font-weight:bold;'>총 입금</td><td style='text-align:right;font-weight:bold;color:#4ade80;'>+{fmt_krw(total_in)}</td><td style='text-align:right;'>100%</td></tr>"

            # 출금 섹션
            rows_html += "<tr style='background:#1e3a5f;'><td colspan='3' style='font-weight:bold;color:#f87171;padding:6px 12px;'>📤 지출</td></tr>"
            for cat, amt in out_df.items():
                pct = f"{amt/total_out*100:.1f}%" if total_out > 0 else "—"
                rows_html += f"<tr><td style='padding:4px 12px;'>&nbsp;&nbsp;{cat}</td><td style='text-align:right;color:#f87171;'>-{fmt_krw(amt)}</td><td style='text-align:right;color:#9ca3af;'>{pct}</td></tr>"
            rows_html += f"<tr style='border-top:2px solid #374151;'><td style='padding:6px 12px;font-weight:bold;'>총 지출</td><td style='text-align:right;font-weight:bold;color:#f87171;'>-{fmt_krw(total_out)}</td><td style='text-align:right;'>100%</td></tr>"

            # 순 변화
            net_color = "#4ade80" if net >= 0 else "#f87171"
            net_sign = "+" if net >= 0 else ""
            rows_html += f"<tr style='border-top:3px solid #60a5fa;background:#0f172a;'><td style='padding:8px 12px;font-weight:bold;font-size:15px;'>순 현금 변화</td><td style='text-align:right;font-weight:bold;font-size:15px;color:{net_color};'>{net_sign}{fmt_krw(net)}</td><td></td></tr>"

            st.markdown(f"""
            <table style='width:100%;border-collapse:collapse;font-size:14px;font-family:Malgun Gothic;'>
            <colgroup><col style='width:50%;'><col style='width:30%;'><col style='width:20%;'></colgroup>
            <tr style='background:#1f2937;'>
              <th style='text-align:left;padding:8px 12px;color:#9ca3af;'>항목</th>
              <th style='text-align:right;padding:8px 12px;color:#9ca3af;'>금액</th>
              <th style='text-align:right;padding:8px 12px;color:#9ca3af;'>비중</th>
            </tr>
            {rows_html}
            </table>
            """, unsafe_allow_html=True)
            st.caption("비중(%) = 입금 합계 대비")

            # 이자 이체 일정
            st.markdown("---")
            st.subheader("📅 이자·상환 이체 일정")
            if not debts_df.empty and "다음상환일" in debts_df.columns:
                sel_year = int(sel_ym[:4])
                sel_month = int(sel_ym[5:7])
                debts_df["상환일_dt"] = pd.to_datetime(debts_df["다음상환일"], errors="coerce")
                debts_df["이체일"] = debts_df["상환일_dt"].apply(lambda d: d.day if pd.notna(d) else None)
                schedule = debts_df[debts_df["이체일"].notna()].sort_values("이체일")

                if not schedule.empty:
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
                                value=fmt_krw(amt),
                                delta=dday if dday else None,
                                delta_color="off",
                            )

                    total_outflow = schedule["월상환액"].fillna(0).sum()
                    col_c1, col_c2, col_c3 = st.columns(3)
                    col_c1.metric("이번 달 총 입금", fmt_krw(total_in))
                    col_c2.metric("이자·상환 합계", fmt_krw(total_outflow))
                    remaining = total_in - total_outflow
                    col_c3.metric(
                        "입금 대비 여유",
                        fmt_krw(remaining),
                        delta="⚠️ 부족" if remaining < 5e7 else "✅ 여유",
                        delta_color="inverse" if remaining < 5e7 else "off",
                    )
            else:
                st.info("대출 정보를 데이터 허브에서 입력해주세요.")

# ── 연간 탭 ──────────────────────────────────────────────────────────────────
with tab_annual:
    all_years = sorted(set(m[:4] for m in months), reverse=True) if months else []
    if not all_years:
        st.info("데이터가 없습니다.")
    else:
        sel_year_ann = st.selectbox("연도", all_years, index=0, key="cf_year")
        year_months = sorted([m for m in months if m.startswith(sel_year_ann)])

        if not year_months:
            st.warning(f"{sel_year_ann}년 데이터 없음")
        else:
            with st.spinner(f"{sel_year_ann}년 현금흐름 집계 중..."):
                monthly_data = {}
                for ym in year_months:
                    cf_tmp = load_cashflow_detail(ym)
                    if not cf_tmp.empty:
                        monthly_data[ym] = cf_tmp

            if not monthly_data:
                st.warning("현금흐름 데이터 없음")
            else:
                # 항목 × 월 피벗
                IN_CATS  = ["매출채권 수금", "어음 추심", "기타 입금"]
                OUT_CATS = ["원재료 결제", "부재료 결제", "인건비 이체", "이자 이체",
                            "설비투자", "미지급금 결제", "부가세 납부", "대출 상환", "기타 지출"]

                rows = []
                for cat in IN_CATS + ["총 입금"] + OUT_CATS + ["총 지출", "순 현금 변화"]:
                    row = {"항목": cat}
                    total_val = 0
                    for ym in year_months:
                        cf_tmp = monthly_data.get(ym)
                        if cf_tmp is None:
                            row[ym[5:7] + "월"] = 0
                            continue
                        if cat == "총 입금":
                            val = cf_tmp[cf_tmp["차변"] > 0]["차변"].sum()
                        elif cat == "총 지출":
                            val = cf_tmp[cf_tmp["대변"] > 0]["대변"].sum()
                        elif cat == "순 현금 변화":
                            val = (cf_tmp[cf_tmp["차변"] > 0]["차변"].sum()
                                   - cf_tmp[cf_tmp["대변"] > 0]["대변"].sum())
                        elif cat in IN_CATS:
                            val = cf_tmp[(cf_tmp["유형"] == cat) & (cf_tmp["차변"] > 0)]["차변"].sum()
                        else:
                            val = cf_tmp[(cf_tmp["유형"] == cat) & (cf_tmp["대변"] > 0)]["대변"].sum()
                        row[ym[5:7] + "월"] = round(val)
                        total_val += val
                    row["연간합계"] = round(total_val)
                    rows.append(row)

                ann_df = pd.DataFrame(rows)
                month_cols = [ym[5:7] + "월" for ym in year_months]

                def fmt_cell(v, cat):
                    if v == 0:
                        return "—"
                    if cat in ("순 현금 변화",):
                        sign = "+" if v > 0 else ""
                        return sign + fmt_krw(v)
                    return fmt_krw(v)

                display_ann = ann_df.copy()
                for col in month_cols + ["연간합계"]:
                    display_ann[col] = display_ann.apply(
                        lambda r: fmt_cell(r[col], r["항목"]), axis=1
                    )

                # 섹션 구분 강조 행
                highlight_rows = {"총 입금", "총 지출", "순 현금 변화"}

                st.dataframe(
                    display_ann,
                    use_container_width=True, hide_index=True,
                    column_order=["항목"] + month_cols + ["연간합계"],
                )

                # 추이 차트 (총 입금 / 총 지출 / 순 변화)
                st.markdown("---")
                st.subheader("📈 연간 현금흐름 추이")

                chart_rows = {
                    "총 입금": ann_df[ann_df["항목"] == "총 입금"].iloc[0] if "총 입금" in ann_df["항목"].values else None,
                    "총 지출": ann_df[ann_df["항목"] == "총 지출"].iloc[0] if "총 지출" in ann_df["항목"].values else None,
                    "순 현금 변화": ann_df[ann_df["항목"] == "순 현금 변화"].iloc[0] if "순 현금 변화" in ann_df["항목"].values else None,
                }

                fig_ann = go.Figure()
                colors = {"총 입금": "#4ade80", "총 지출": "#f87171", "순 현금 변화": "#60a5fa"}
                for label, row_data in chart_rows.items():
                    if row_data is None:
                        continue
                    vals = [row_data.get(c, 0) / 1e6 for c in month_cols]
                    fig_ann.add_trace(go.Bar(
                        name=label,
                        x=month_cols,
                        y=vals,
                        marker_color=colors[label],
                        text=[f"{v:.0f}" for v in vals],
                        textposition="outside",
                    ))

                fig_ann.update_layout(
                    barmode="group",
                    xaxis_title="월", yaxis_title="금액 (백만원)",
                    height=350, margin=dict(t=20, b=40),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_ann, use_container_width=True)

st.markdown("---")

# ── 섹션 3: 이자비용 월 추이 ──────────────────────────────────────────────────
st.header("📈 이자비용 월 추이 (103 실제 이체 기준)")

if interest_hist.empty:
    st.info("분개장 데이터가 없습니다.")
else:
    fig = go.Figure(go.Bar(
        x=interest_hist["ym"], y=interest_hist["차변"] / 1e6,
        name="이자비용", marker_color="#ef4444",
        text=(interest_hist["차변"] / 1e6).apply(lambda v: f"{v:,.1f}"),
        textposition="outside",
    ))
    fig.update_layout(
        xaxis_title="월", yaxis_title="이자비용 (백만원)",
        height=300, margin=dict(t=20, b=40),
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
            fig2.add_trace(go.Bar(x=sub["ym"], y=sub["차변"]/1e6, name=label, marker_color=colors[code]))
    fig2.update_layout(
        barmode="stack", xaxis_title="월", yaxis_title="설비투자 (백만원)",
        height=280, margin=dict(t=10, b=40),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
    )
    st.plotly_chart(fig2, use_container_width=True)

    recent = capex_hist.sort_values("전표일자", ascending=False).head(10)
    recent["금액"] = recent["차변"].apply(fmt_krw)
    recent["구분"] = recent["계정코드"].map(code_label)
    st.dataframe(
        recent[["전표일자", "구분", "금액", "거래처", "적요"]].reset_index(drop=True),
        use_container_width=True, hide_index=True,
    )

st.markdown("---")

# ── 섹션 5: 투자 시뮬레이터 ──────────────────────────────────────────────────
st.header("🧮 설비투자 자금 부담 시뮬레이터")

with st.expander("시뮬레이터 열기", expanded=False):
    col_in1, col_in2, col_res = st.columns([1, 1, 1])
    with col_in1:
        invest_amt = st.number_input("설비투자 금액 (억)", value=11.0, min_value=0.0, step=0.5)
        loan_ratio = st.slider("대출 비율 (%)", 0, 100, 70)
        loan_rate  = st.number_input("대출 금리 (%)", value=3.5, min_value=0.0, max_value=20.0, step=0.1)
    with col_in2:
        loan_years  = st.number_input("대출 기간 (년)", value=5, min_value=1, max_value=20)
        repay_type  = st.radio("상환 방식", ["만기 일시상환", "원금균등분할"])

    loan_amt = invest_amt * loan_ratio / 100
    monthly_interest_new = loan_amt * 1e8 * (loan_rate / 100) / 12
    monthly_total_new = monthly_interest_new + (loan_amt * 1e8 / (loan_years * 12) if repay_type == "원금균등분할" else 0)

    with col_res:
        st.markdown("#### 결과")
        st.metric("신규 대출액", fmt_krw(loan_amt * 1e8))
        st.metric("자체 자금", fmt_krw((invest_amt - loan_amt) * 1e8))
        st.metric("추가 월 부담", fmt_krw(monthly_total_new))
        st.metric("총 월 부담", fmt_krw(total_monthly + monthly_total_new),
                  delta=f"+{fmt_krw(monthly_total_new)}", delta_color="inverse")

st.markdown("---")

# ── 섹션 6: 대출 현황 ────────────────────────────────────────────────────────
st.header("🏦 대출 현황")

if debts_df.empty:
    st.info("데이터 허브 → 대출 정보 탭에서 입력해 주세요.")
else:
    display_cols = [c for c in ["은행명","대출종류","원금잔액","금리","만기일","다음상환일","월상환액","비고"] if c in debts_df.columns]
    display_df = debts_df[display_cols].copy()
    if "원금잔액" in display_df.columns:
        display_df["원금잔액"] = display_df["원금잔액"].apply(lambda v: fmt_krw(v) if pd.notna(v) and v > 0 else "-")
    if "금리" in display_df.columns:
        display_df["금리"] = display_df["금리"].apply(lambda v: f"{v:.2f}%" if pd.notna(v) and v > 0 else "-")
    if "월상환액" in display_df.columns:
        display_df["월상환액"] = display_df["월상환액"].apply(lambda v: fmt_krw(v) if pd.notna(v) and v > 0 else "-")

    def row_style(row):
        try:
            d = debts_df.loc[row.name, "만기일_dt"]
            if pd.isna(d): return [""] * len(row)
            days = (d.date() - today).days
            if days < 0: return ["background-color: #fee2e2"] * len(row)
            if days <= 90: return ["background-color: #fef9c3"] * len(row)
        except Exception:
            pass
        return [""] * len(row)

    st.dataframe(display_df.style.apply(row_style, axis=1), use_container_width=True, hide_index=True)
    col_t1, col_t2 = st.columns(2)
    col_t1.metric("총 대출 잔액", fmt_krw(total_principal))
    col_t2.metric("월 이자·상환 합계", fmt_krw(total_monthly))

st.markdown("---")
st.caption("💡 **다음달 챙길 일** — 만기 도래 대출 연장 협의, 이자 이체일 전 자금 확보")
