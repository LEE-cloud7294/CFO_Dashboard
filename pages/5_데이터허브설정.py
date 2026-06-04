import streamlit as st
import pandas as pd
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.loader import load_excel, clean_journal, add_ym_column
from core.db import (upsert_journal, get_available_months,
                     load_master_blacklist, add_blacklist,
                     load_debts)
from core.metrics import calc_kpi

st.set_page_config(page_title="데이터 허브", page_icon="⚙️", layout="wide")

# 비밀번호 체크
if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.title("⚙️ 데이터 허브 & 설정")

tab1, tab2, tab3 = st.tabs(["📤 분개장 업로드", "🚫 블랙리스트", "🏦 대출 정보"])

# ─── TAB 1: 분개장 업로드 ───────────────────────────────────────────────
with tab1:
    st.subheader("위하고 분개장 업로드")
    st.info(
        "월별 파일 또는 여러 달이 포함된 파일 모두 가능합니다. "
        "여러 달이 감지되면 **월별로 자동 분할하여 각각 저장**합니다. "
        "같은 달을 다시 올리면 자동으로 덮어씁니다."
    )

    months = get_available_months()
    if months:
        st.markdown(f"**현재 저장된 월:** {', '.join(months)}")
    else:
        st.markdown("**현재 저장된 월:** 없음 (첫 업로드)")

    uploaded = st.file_uploader(
        "분개장 파일 선택 (xlsx / xls / csv)",
        type=["xlsx", "xls", "csv"],
        key="journal_upload"
    )

    if uploaded:
        with st.spinner("파일 읽는 중..."):
            try:
                raw_df = load_excel(uploaded)
                df = clean_journal(raw_df)
            except Exception as e:
                st.error(f"파일 읽기 오류: {e}")
                st.stop()

        # 월별 분할 감지
        df["_ym"] = pd.to_datetime(df["전표일자"], errors="coerce").dt.to_period("M").astype(str)
        month_groups = df.groupby("_ym").size().sort_index()
        detected_months = month_groups.index.tolist()

        if len(detected_months) == 1:
            st.success(f"파일 읽기 완료 — 연월: **{detected_months[0]}**, 유효 분개행: **{len(df):,}개**")
        else:
            st.success(f"파일 읽기 완료 — **{len(detected_months)}개월** 감지, 유효 분개행: **{len(df):,}개**")
            st.info(f"자동 분할 예정 월: {', '.join(detected_months)}")

        # KPI 미리보기 (전체 합산)
        kpi_df = df.copy()
        kpi_df["전표일자"] = pd.to_datetime(kpi_df["전표일자"], errors="coerce")
        kpi_df["계정그룹"] = kpi_df["계정코드"].astype(str).str[:1]
        kpi = calc_kpi(kpi_df)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("매출액 (합산)", f"{kpi['매출액']/1e8:.2f}억원")
        col2.metric("영업이익 (합산)", f"{kpi['영업이익']/1e8:.2f}억원")
        col3.metric("영업이익률", f"{kpi['영업이익률']:.1f}%")
        col4.metric("인건비율", f"{kpi['인건비율']:.1f}%")

        # 월별 행수 미리보기
        with st.expander("월별 분할 미리보기"):
            preview = month_groups.reset_index()
            preview.columns = ["연월", "분개행수"]
            st.dataframe(preview, use_container_width=True, hide_index=True)

        # 데이터 미리보기
        with st.expander("데이터 미리보기 (상위 20행)"):
            st.dataframe(df.drop(columns=["_ym"]).head(20), use_container_width=True)

        st.divider()

        overlap = [m for m in detected_months if m in months]
        if overlap:
            st.warning(f"⚠️ 이미 저장된 월이 포함되어 있습니다: {', '.join(overlap)} → 덮어씁니다.")

        if st.button("☁️ Supabase에 저장 (월별 자동 분할)", type="primary", use_container_width=True):
            progress = st.progress(0)
            results = []
            for i, ym in enumerate(detected_months):
                with st.spinner(f"{ym} 저장 중..."):
                    try:
                        df_month = df[df["_ym"] == ym].drop(columns=["_ym"]).copy()
                        df_month = add_ym_column(df_month, ym)
                        count = upsert_journal(df_month, ym)
                        results.append(f"✅ {ym}: {count:,}건")
                    except Exception as e:
                        results.append(f"❌ {ym}: 오류 — {e}")
                progress.progress((i + 1) / len(detected_months))

            st.success("저장 완료!")
            for r in results:
                st.markdown(r)
            st.balloons()
            st.rerun()

# ─── TAB 2: 블랙리스트 ──────────────────────────────────────────────────
with tab2:
    st.subheader("블랙리스트 (파산·휴면 거래처)")
    st.caption("블랙리스트 업체는 매출채권 화면에서 필터로 숨길 수 있습니다.")

    blacklist = load_master_blacklist()
    if blacklist:
        st.dataframe(
            pd.DataFrame({"거래처명": blacklist}),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("등록된 블랙리스트가 없습니다.")

    with st.form("blacklist_form"):
        new_name = st.text_input("추가할 거래처명")
        if st.form_submit_button("추가"):
            if new_name.strip():
                add_blacklist(new_name.strip())
                st.success(f"'{new_name}' 추가됨")
                st.rerun()

# ─── TAB 3: 대출 정보 ───────────────────────────────────────────────────
with tab3:
    st.subheader("대출 정보 (페이지 4 자금달력 연동)")
    st.caption("분개장에서 자동 추출된 이자·차입금 외에, 만기일·금리 등 미래 정보를 여기서 입력합니다.")

    debts_df = load_debts()
    if not debts_df.empty:
        show_cols = ["은행명", "대출종류", "원금잔액", "금리", "만기일", "다음상환일", "월상환액", "비고"]
        show_cols = [c for c in show_cols if c in debts_df.columns]
        st.dataframe(debts_df[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("등록된 대출 정보가 없습니다. 아래에서 추가하세요.")

    with st.expander("대출 정보 추가"):
        with st.form("debt_form"):
            c1, c2 = st.columns(2)
            bank = c1.text_input("은행명 (예: 국민은행)")
            kind = c2.text_input("대출 종류 (예: 운전자금)")
            c3, c4 = st.columns(2)
            principal = c3.number_input("원금잔액 (원)", min_value=0, step=1000000)
            rate = c4.number_input("금리 (%)", min_value=0.0, max_value=30.0, step=0.1)
            c5, c6, c7 = st.columns(3)
            maturity = c5.date_input("만기일")
            next_pay = c6.date_input("다음 상환일")
            monthly = c7.number_input("월 상환액 (원)", min_value=0, step=100000)
            note = st.text_input("비고")

            if st.form_submit_button("저장"):
                from core.db import get_client
                get_client().table("debts").insert({
                    "은행명": bank, "대출종류": kind,
                    "원금잔액": principal, "금리": rate,
                    "만기일": str(maturity), "다음상환일": str(next_pay),
                    "월상환액": monthly, "비고": note,
                }).execute()
                st.success("저장 완료")
                st.rerun()
