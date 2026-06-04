import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_debts, load_all_journal, get_client

st.set_page_config(page_title="부채·이자 관리", page_icon="🏦", layout="wide")

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("🏦 부채·이자 관리")
st.caption("대출별 금리 비교 · 실적 이자 검증 · 이자 절감 시뮬레이터 · 대출 정보 편집")

today = date.today()


# ─── 데이터 로드 ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_debts_data():
    return load_debts()


@st.cache_data(ttl=300)
def load_interest_monthly():
    """931(이자비용) 분개장 기준 월별 이자 합계."""
    df = load_all_journal()
    if df.empty:
        return pd.DataFrame(columns=["ym", "차변"])
    interest = df[df["계정코드"].astype(str) == "931"][["전표일자", "차변"]].copy()
    if interest.empty:
        return pd.DataFrame(columns=["ym", "차변"])
    interest["ym"] = interest["전표일자"].dt.strftime("%Y-%m")
    return interest.groupby("ym")["차변"].sum().reset_index()


debts_df = load_debts_data()
interest_hist = load_interest_monthly()

# ─── 상단 KPI ────────────────────────────────────────────────────────────────
total_principal = debts_df["원금잔액"].fillna(0).sum() if not debts_df.empty else 0
total_monthly = debts_df["월상환액"].fillna(0).sum() if not debts_df.empty else 0

# 가중평균 금리
if not debts_df.empty and total_principal > 0:
    debts_with_rate = debts_df[debts_df["원금잔액"].fillna(0) > 0].copy()
    if not debts_with_rate.empty:
        weighted_rate = (
            (debts_with_rate["금리"].fillna(0) * debts_with_rate["원금잔액"].fillna(0)).sum()
            / debts_with_rate["원금잔액"].fillna(0).sum()
        )
    else:
        weighted_rate = 0.0
else:
    weighted_rate = 0.0

# 분개장 최근 3개월 평균 이자
recent_interest_avg = 0
if not interest_hist.empty and len(interest_hist) >= 1:
    recent_interest_avg = interest_hist.tail(3)["차변"].mean()

# 예상 vs 실적 괴리
expected_annual = total_monthly * 12
actual_annual = recent_interest_avg * 12
gap = actual_annual - expected_annual

c1, c2, c3, c4 = st.columns(4)
c1.metric("총 대출 잔액", f"{total_principal / 1e8:.1f}억 원")
c2.metric("가중평균 금리", f"{weighted_rate:.2f}%" if weighted_rate else "—")
c3.metric("월 이자·상환 합계", f"{total_monthly / 10000:,.0f}만 원")
c4.metric(
    "분개장 실적 이자 (3개월 평균)",
    f"{recent_interest_avg / 10000:,.0f}만 원",
    delta=f"예상 대비 {gap / 10000:+,.0f}만원/年" if gap else None,
    delta_color="inverse" if gap > 0 else "normal",
)

st.markdown("---")

# ─── 탭 구성 ─────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 대출별 분석", "📈 이자비용 추이", "✂️ 이자 절감 시뮬레이터", "✏️ 대출 정보 편집"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: 대출별 분석
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    if debts_df.empty:
        st.info("데이터 허브 → 대출 정보 탭에서 대출 내역을 입력해 주세요.")
    else:
        # 만기 D-day 계산
        debts_df["만기일_dt"] = pd.to_datetime(debts_df["만기일"], errors="coerce")
        debts_df["D_day"] = debts_df["만기일_dt"].apply(
            lambda d: (d.date() - today).days if pd.notna(d) else None
        )
        debts_df["만기상태"] = debts_df["D_day"].apply(
            lambda d: "🚨 초과" if d is not None and d < 0
            else ("🔴 30일 이내" if d is not None and d <= 30
                  else ("🟡 90일 이내" if d is not None and d <= 90
                        else "🟢 안전"))
        )

        # 금리 높은 순 정렬
        sorted_df = debts_df.sort_values("금리", ascending=False)

        # 대출별 연이자 계산
        sorted_df["연이자_계산"] = sorted_df["원금잔액"].fillna(0) * sorted_df["금리"].fillna(0) / 100
        sorted_df["월이자_계산"] = sorted_df["연이자_계산"] / 12

        # ── 금리 순위 막대 차트 ──────────────────────────────────────────────
        fig_rate = go.Figure()
        fig_rate.add_trace(go.Bar(
            y=sorted_df["은행명"].fillna("미입력") + " " + sorted_df["대출종류"].fillna(""),
            x=sorted_df["금리"].fillna(0),
            orientation="h",
            marker_color=sorted_df["금리"].apply(
                lambda r: "#ef4444" if r >= 4 else ("#f97316" if r >= 3.5 else "#22c55e")
            ),
            text=sorted_df["금리"].apply(lambda r: f"{r:.2f}%"),
            textposition="outside",
        ))
        fig_rate.update_layout(
            title="대출별 금리 (높은 순)",
            xaxis_title="금리 (%)",
            height=max(200, len(sorted_df) * 55),
            margin=dict(t=50, b=30, l=160),
        )
        st.plotly_chart(fig_rate, use_container_width=True)

        # ── 대출별 이자 파이 차트 ─────────────────────────────────────────────
        pie_df = sorted_df[sorted_df["월이자_계산"] > 0].copy()
        if not pie_df.empty:
            labels = pie_df["은행명"].fillna("미입력") + "\n" + pie_df["대출종류"].fillna("")
            fig_pie = px.pie(
                pie_df,
                values="월이자_계산",
                names=labels,
                title="월이자 부담 구성",
                color_discrete_sequence=px.colors.sequential.RdBu_r,
            )
            fig_pie.update_traces(textinfo="percent+label")
            fig_pie.update_layout(height=350, margin=dict(t=50, b=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        # ── 대출 현황 테이블 ──────────────────────────────────────────────────
        st.subheader("대출별 상세 현황")

        display = sorted_df[[
            "은행명", "대출종류", "원금잔액", "금리", "월이자_계산", "만기일", "만기상태", "D_day", "비고"
        ]].copy()
        display["원금잔액"] = display["원금잔액"].apply(
            lambda v: f"{v / 1e8:.1f}억" if pd.notna(v) and v > 0 else "-"
        )
        display["금리"] = display["금리"].apply(
            lambda v: f"{v:.2f}%" if pd.notna(v) and v > 0 else "-"
        )
        display["월이자_계산"] = display["월이자_계산"].apply(
            lambda v: f"{v / 10000:,.0f}만원" if pd.notna(v) and v > 0 else "-"
        )
        display["D_day"] = display["D_day"].apply(
            lambda d: f"D{int(d):+d}" if pd.notna(d) else "-"
        )
        display = display.rename(columns={"월이자_계산": "월이자(계산)", "D_day": "만기D-day"})

        def row_style(row):
            idx = row.name
            try:
                d = debts_df.loc[idx, "D_day"]
                if d is not None and d < 0:
                    return ["background-color: #fee2e2"] * len(row)
                if d is not None and d <= 90:
                    return ["background-color: #fef9c3"] * len(row)
            except Exception:
                pass
            return [""] * len(row)

        st.dataframe(display.style.apply(row_style, axis=1), use_container_width=True, hide_index=True)

        # 합계 행
        total_monthly_calc = sorted_df["월이자_계산"].sum()
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("총 잔액 합계", f"{total_principal / 1e8:.1f}억 원")
        col_s2.metric("월이자 합계 (계산)", f"{total_monthly_calc / 10000:,.0f}만 원")
        col_s3.metric("연이자 합계 (계산)", f"{total_monthly_calc * 12 / 10000:,.0f}만 원")

        # ── 상위 금리 대출 우선 상환 추천 ─────────────────────────────────────
        st.markdown("---")
        high_rate = sorted_df[sorted_df["금리"].fillna(0) >= 3.7]
        if not high_rate.empty:
            st.warning(
                "💡 **고금리 대출 우선 상환 추천** — "
                + ", ".join(
                    f"{r['은행명']} {r['대출종류']} ({r['금리']:.2f}%)"
                    for _, r in high_rate.iterrows()
                )
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: 이자비용 추이
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    if interest_hist.empty:
        st.info("분개장 데이터가 없습니다. (931 이자비용 계정)")
    else:
        # 예상 이자선 (debts 테이블 기준)
        expected_monthly = total_monthly  # debts 입력값 기준

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=interest_hist["ym"],
            y=interest_hist["차변"] / 10000,
            name="실적 이자 (분개장)",
            marker_color="#ef4444",
            text=(interest_hist["차변"] / 10000).apply(lambda v: f"{v:,.0f}"),
            textposition="outside",
        ))
        if expected_monthly > 0:
            fig.add_hline(
                y=expected_monthly / 10000,
                line_dash="dot",
                line_color="#f97316",
                annotation_text=f"예상 이자 {expected_monthly / 10000:,.0f}만원",
                annotation_position="top right",
            )
        fig.update_layout(
            title="월별 이자비용 — 실적 vs 예상",
            xaxis_title="월",
            yaxis_title="이자비용 (만원)",
            height=380,
            margin=dict(t=60, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)

        # 통계
        avg = interest_hist["차변"].mean()
        max_val = interest_hist["차변"].max()
        min_val = interest_hist["차변"].min()

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("평균 월이자 (실적)", f"{avg / 10000:,.0f}만 원")
        col_b.metric("최대 월이자", f"{max_val / 10000:,.0f}만 원")
        col_c.metric("최소 월이자", f"{min_val / 10000:,.0f}만 원")

        st.markdown("---")
        st.subheader("월별 이자 상세")
        hist_display = interest_hist.copy()
        hist_display["이자비용"] = hist_display["차변"].apply(lambda v: f"{v / 10000:,.0f}만원")
        if expected_monthly > 0:
            hist_display["예상 대비"] = hist_display["차변"].apply(
                lambda v: f"{(v - expected_monthly) / 10000:+,.0f}만원"
            )
        st.dataframe(
            hist_display[["ym", "이자비용"] + (["예상 대비"] if expected_monthly > 0 else [])].rename(columns={"ym": "월"}),
            use_container_width=True,
            hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: 이자 절감 시뮬레이터
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("✂️ 특정 대출 일부 상환 시 이자 절감 계산")

    if debts_df.empty:
        st.info("대출 정보를 먼저 입력해 주세요.")
    else:
        options = []
        for _, row in debts_df.iterrows():
            label = f"{row.get('은행명', '')} {row.get('대출종류', '')} ({row.get('금리', 0):.2f}%, {row.get('원금잔액', 0) / 1e8:.1f}억)"
            options.append((label, row))

        sel_label = st.selectbox("상환할 대출 선택", [o[0] for o in options])
        sel_row = next(row for label, row in options if label == sel_label)

        col_sim1, col_sim2 = st.columns(2)
        with col_sim1:
            current_principal = float(sel_row.get("원금잔액", 0) or 0)
            repay_amt = st.number_input(
                "상환 금액 (억)",
                min_value=0.0,
                max_value=current_principal / 1e8,
                value=min(1.0, current_principal / 1e8),
                step=0.5,
            )
            rate = float(sel_row.get("금리", 0) or 0)

        remaining = current_principal - repay_amt * 1e8
        current_monthly_interest = current_principal * rate / 100 / 12
        new_monthly_interest = remaining * rate / 100 / 12
        monthly_saving = current_monthly_interest - new_monthly_interest
        annual_saving = monthly_saving * 12

        with col_sim2:
            st.markdown("#### 절감 효과")
            st.metric("현재 월이자", f"{current_monthly_interest / 10000:,.0f}만원")
            st.metric("상환 후 월이자", f"{new_monthly_interest / 10000:,.0f}만원")
            st.metric(
                "월 절감액",
                f"{monthly_saving / 10000:,.0f}만원",
                delta=f"연 {annual_saving / 10000:,.0f}만원 절감",
                delta_color="normal",
            )

        if repay_amt > 0:
            payback_years = (repay_amt * 1e8) / annual_saving if annual_saving > 0 else None
            st.info(
                f"💡 {repay_amt:.1f}억 상환 시 → "
                f"월 {monthly_saving / 10000:,.0f}만원 · 연 {annual_saving / 10000:,.0f}만원 절감"
                + (f" (원금 회수 기간: 약 {payback_years:.1f}년)" if payback_years else "")
            )

        # 전체 대출 절감 비교
        st.markdown("---")
        st.subheader("전체 대출 금리별 절감 효과 비교")
        debts_df["월이자_계산"] = debts_df["원금잔액"].fillna(0) * debts_df["금리"].fillna(0) / 100 / 12
        debts_df["1억 상환 시 월절감"] = debts_df["금리"].fillna(0) / 100 / 12 * 1e8

        compare_df = debts_df[["은행명", "대출종류", "금리", "원금잔액", "월이자_계산", "1억 상환 시 월절감"]].copy()
        compare_df["금리"] = compare_df["금리"].apply(lambda v: f"{v:.2f}%")
        compare_df["원금잔액"] = compare_df["원금잔액"].apply(lambda v: f"{v / 1e8:.1f}억")
        compare_df["월이자_계산"] = compare_df["월이자_계산"].apply(lambda v: f"{v / 10000:,.0f}만원")
        compare_df["1억 상환 시 월절감"] = compare_df["1억 상환 시 월절감"].apply(lambda v: f"{v / 10000:,.1f}만원")
        compare_df = compare_df.rename(columns={"월이자_계산": "현재 월이자"})
        st.dataframe(compare_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: 대출 정보 편집
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("대출 정보 추가 · 수정 · 삭제")
    st.caption("금리·만기일·월상환액 변경 시 여기서 업데이트합니다.")

    debts_fresh = load_debts()

    # ── 현재 목록 ─────────────────────────────────────────────────────────────
    if not debts_fresh.empty:
        show_cols = [c for c in
                     ["id", "은행명", "대출종류", "원금잔액", "금리", "만기일", "다음상환일", "월상환액", "비고"]
                     if c in debts_fresh.columns]
        st.dataframe(debts_fresh[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("등록된 대출 정보가 없습니다.")

    # ── 추가 ──────────────────────────────────────────────────────────────────
    with st.expander("➕ 대출 추가"):
        with st.form("debt_add_form"):
            c1, c2 = st.columns(2)
            bank = c1.text_input("은행명")
            kind = c2.text_input("대출 종류")
            c3, c4 = st.columns(2)
            principal = c3.number_input("원금잔액 (원)", min_value=0, step=1_000_000)
            rate = c4.number_input("금리 (%)", min_value=0.0, max_value=30.0, step=0.01)
            c5, c6, c7 = st.columns(3)
            maturity = c5.date_input("만기일", key="add_maturity")
            next_pay = c6.date_input("다음 상환일", key="add_nextpay")
            monthly = c7.number_input("월 상환액 (원)", min_value=0, step=100_000)
            note = st.text_input("비고")
            if st.form_submit_button("저장"):
                get_client().table("debts").insert({
                    "은행명": bank, "대출종류": kind,
                    "원금잔액": principal, "금리": rate,
                    "만기일": str(maturity), "다음상환일": str(next_pay),
                    "월상환액": monthly, "비고": note,
                }).execute()
                st.success("저장 완료")
                st.cache_data.clear()
                st.rerun()

    # ── 수정 ──────────────────────────────────────────────────────────────────
    if not debts_fresh.empty and "id" in debts_fresh.columns:
        with st.expander("✏️ 대출 수정"):
            opts = {
                f"{r.get('은행명','')} {r.get('대출종류','')}": r
                for _, r in debts_fresh.iterrows()
            }
            sel = st.selectbox("수정할 대출 선택", list(opts.keys()), key="edit_sel")
            sel_row = opts[sel]

            with st.form("debt_edit_form"):
                c1, c2 = st.columns(2)
                bank_e = c1.text_input("은행명", value=str(sel_row.get("은행명", "") or ""))
                kind_e = c2.text_input("대출 종류", value=str(sel_row.get("대출종류", "") or ""))
                c3, c4 = st.columns(2)
                principal_e = c3.number_input("원금잔액 (원)", value=int(sel_row.get("원금잔액", 0) or 0), step=1_000_000)
                rate_e = c4.number_input("금리 (%)", value=float(sel_row.get("금리", 0) or 0), step=0.01)
                c5, c6, c7 = st.columns(3)
                try:
                    mat_val = pd.to_datetime(sel_row.get("만기일")).date()
                except Exception:
                    mat_val = today
                try:
                    pay_val = pd.to_datetime(sel_row.get("다음상환일")).date()
                except Exception:
                    pay_val = today
                maturity_e = c5.date_input("만기일", value=mat_val, key="edit_maturity")
                next_pay_e = c6.date_input("다음 상환일", value=pay_val, key="edit_nextpay")
                monthly_e = c7.number_input("월 상환액 (원)", value=int(sel_row.get("월상환액", 0) or 0), step=100_000)
                note_e = st.text_input("비고", value=str(sel_row.get("비고", "") or ""))

                if st.form_submit_button("수정 저장"):
                    get_client().table("debts").update({
                        "은행명": bank_e, "대출종류": kind_e,
                        "원금잔액": principal_e, "금리": rate_e,
                        "만기일": str(maturity_e), "다음상환일": str(next_pay_e),
                        "월상환액": monthly_e, "비고": note_e,
                    }).eq("id", sel_row["id"]).execute()
                    st.success("수정 완료")
                    st.cache_data.clear()
                    st.rerun()

        # ── 삭제 ──────────────────────────────────────────────────────────────
        with st.expander("🗑️ 대출 삭제"):
            opts_del = {
                f"{r.get('은행명','')} {r.get('대출종류','')}": r
                for _, r in debts_fresh.iterrows()
            }
            sel_del = st.selectbox("삭제할 대출 선택", list(opts_del.keys()), key="del_sel")
            sel_del_row = opts_del[sel_del]
            st.warning(f"'{sel_del}' 을(를) 삭제합니다. 복구 불가.")
            if st.button("삭제 확인", type="primary"):
                get_client().table("debts").delete().eq("id", sel_del_row["id"]).execute()
                st.success("삭제 완료")
                st.cache_data.clear()
                st.rerun()

st.markdown("---")
st.caption("💡 **다음달 뭘 할까** — 고금리 대출 우선 상환 검토, 만기 연장 협의, 이자 절감으로 운전자금 여유 확보")
