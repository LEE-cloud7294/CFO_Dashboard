import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.loader import load_excel, clean_journal, add_ym_column
from core.db import (
    upsert_journal, get_available_months,
    load_master_blacklist, add_blacklist, load_debts,
    upsert_tax_journal, load_tax_depreciation, get_tax_years,
    TAX_JOURNAL_SQL,
)
from core.metrics import calc_kpi

st.set_page_config(page_title="데이터 허브", page_icon="⚙️", layout="wide")

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

tab1, tab2, tab3, tab4 = st.tabs([
    "📤 분개장 업로드",
    "📊 세무사 분개장 (감가상각)",
    "🚫 블랙리스트",
    "🏦 대출 정보",
])

# ── TAB 1: 분개장 업로드 ─────────────────────────────────────────────────────
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

        df["_ym"] = pd.to_datetime(df["전표일자"], errors="coerce").dt.to_period("M").astype(str)
        month_groups = df.groupby("_ym").size().sort_index()
        detected_months = month_groups.index.tolist()

        if len(detected_months) == 1:
            st.success(f"파일 읽기 완료 — 연월: **{detected_months[0]}**, 유효 분개행: **{len(df):,}개**")
        else:
            st.success(f"파일 읽기 완료 — **{len(detected_months)}개월** 감지, 유효 분개행: **{len(df):,}개**")
            st.info(f"자동 분할 예정 월: {', '.join(detected_months)}")

        kpi_df = df.copy()
        kpi_df["전표일자"] = pd.to_datetime(kpi_df["전표일자"], errors="coerce")
        kpi_df["계정그룹"] = kpi_df["계정코드"].astype(str).str[:1]
        kpi = calc_kpi(kpi_df)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("매출액 (합산)", f"{kpi['매출액']/1e8:.2f}억원")
        col2.metric("영업이익 (합산)", f"{kpi['영업이익']/1e8:.2f}억원")
        col3.metric("영업이익률", f"{kpi['영업이익률']:.1f}%")
        col4.metric("인건비율", f"{kpi['인건비율']:.1f}%")

        with st.expander("월별 분할 미리보기"):
            preview = month_groups.reset_index()
            preview.columns = ["연월", "분개행수"]
            st.dataframe(preview, use_container_width=True, hide_index=True)

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

# ── TAB 2: 세무사 분개장 (감가상각) ──────────────────────────────────────────
with tab2:
    st.subheader("세무사 분개장 업로드 — 감가상각 회계기준 뷰 활성화")
    st.info(
        "세무사무소의 연간 결산 분개장(위하고 CSV)을 업로드합니다. "
        "감가상각비(Code 518·818·840) 항목을 자동 추출하여 **÷12 월별 균등 배분**합니다. "
        "업로드 후 손익계산서에서 **회계기준 토글**이 활성화됩니다."
    )

    # 현재 업로드된 연도
    tax_years = get_tax_years()
    if tax_years:
        dep_df = load_tax_depreciation()
        st.markdown("**업로드된 연도:**")
        if not dep_df.empty:
            annual = dep_df.groupby("year")["차변"].sum().reset_index()
            annual.columns = ["연도", "연간감가상각(원)"]
            annual["월별배분(원)"] = annual["연간감가상각(원)"] / 12
            annual["연간감가상각"] = annual["연간감가상각(원)"].apply(lambda v: f"{v/1e8:.3f}억")
            annual["월별배분"] = annual["월별배분(원)"].apply(lambda v: f"{v/1e6:.1f}백만")
            st.dataframe(annual[["연도", "연간감가상각", "월별배분"]], use_container_width=True, hide_index=True)
    else:
        st.warning("아직 세무사 분개장이 업로드되지 않았습니다.")

    # Supabase 테이블 생성 안내
    with st.expander("⚙️ 최초 설정: Supabase tax_journal 테이블 생성"):
        st.caption("Supabase 대시보드 → SQL Editor에서 아래 SQL을 실행하세요. (최초 1회만)")
        st.code(TAX_JOURNAL_SQL, language="sql")

    st.divider()

    # 파일 업로드
    tax_file = st.file_uploader(
        "세무사 분개장 파일 (xlsx / xls / csv)",
        type=["xlsx", "xls", "csv"],
        key="tax_journal_upload"
    )

    if tax_file:
        with st.spinner("파일 읽는 중..."):
            try:
                raw_df = load_excel(tax_file)
                df = clean_journal(raw_df)
            except Exception as e:
                st.error(f"파일 읽기 오류: {e}")
                st.stop()

        # 연도 자동 감지
        df["_year"] = pd.to_datetime(df["전표일자"], errors="coerce").dt.year.astype(str)
        detected_years = sorted(df["_year"].dropna().unique())

        st.success(f"파일 읽기 완료 — 감지 연도: {', '.join(detected_years)}, 분개행: {len(df):,}개")

        # 감가상각 항목만 추출 미리보기
        dep_preview = df[df["계정코드"].astype(str).isin(["518", "818", "840"])].copy()

        if dep_preview.empty:
            st.error("⚠️ 감가상각비(Code 518·818·840) 항목이 없습니다. 파일을 확인해주세요.")
        else:
            st.markdown("**감가상각비 항목 (자동 추출):**")
            by_code = (
                dep_preview.groupby(["계정코드", "계정과목"])["차변"]
                .sum()
                .reset_index()
                .rename(columns={"차변": "금액"})
            )
            by_code["금액"] = by_code["금액"].apply(lambda v: f"{v/1e6:.1f}백만원")
            st.dataframe(by_code, use_container_width=True, hide_index=True)

            total_dep = dep_preview["차변"].sum()
            monthly_dep = total_dep / 12
            c1, c2, c3 = st.columns(3)
            c1.metric("연간 감가상각 총액", f"{total_dep/1e8:.3f}억원")
            c2.metric("월별 균등 배분 (÷12)", f"{monthly_dep/1e6:.1f}백만원")
            c3.metric("적용 연도", ", ".join(detected_years))

            sel_year = st.selectbox("저장할 연도", detected_years, index=len(detected_years)-1)

            if st.button("☁️ tax_journal에 저장", type="primary", use_container_width=True):
                with st.spinner("저장 중..."):
                    try:
                        df_save = df.copy()
                        df_save["year"] = sel_year
                        # 날짜를 문자열로 변환
                        df_save["전표일자"] = df_save["전표일자"].astype(str)
                        count = upsert_tax_journal(df_save, sel_year)
                        st.success(f"✅ {sel_year}년 세무사 분개장 {count:,}건 저장 완료!")
                        st.info(f"손익계산서 페이지에서 '회계기준' 토글 선택 시 월별 {monthly_dep/1e6:.1f}백만원 감가상각이 반영됩니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")
                        st.info("Supabase SQL Editor에서 tax_journal 테이블 생성 후 다시 시도하세요.")

# ── TAB 3: 블랙리스트 ────────────────────────────────────────────────────────
with tab3:
    st.subheader("블랙리스트 (파산·휴면 거래처)")
    st.caption("블랙리스트 업체는 매출채권 화면에서 필터로 숨길 수 있습니다.")

    blacklist = load_master_blacklist()
    if blacklist:
        st.dataframe(pd.DataFrame({"거래처명": blacklist}), use_container_width=True, hide_index=True)
    else:
        st.info("등록된 블랙리스트가 없습니다.")

    with st.form("blacklist_form"):
        new_name = st.text_input("추가할 거래처명")
        if st.form_submit_button("추가"):
            if new_name.strip():
                add_blacklist(new_name.strip())
                st.success(f"'{new_name}' 추가됨")
                st.rerun()

# ── TAB 4: 대출 정보 ──────────────────────────────────────────────────────────
with tab4:
    st.subheader("대출 정보 (자금타임라인 연동)")
    st.caption("만기일·금리·이체일 등 미래 정보를 여기서 입력합니다.")

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
            bank = c1.text_input("은행명")
            kind = c2.text_input("대출 종류")
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
