import streamlit as st
import pandas as pd
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.loader import load_excel, clean_journal, add_ym_column
from core.db import (
    upsert_journal, get_available_months,
    load_master_blacklist, add_blacklist, load_debts,
    upsert_tax_journal, load_tax_depreciation, get_tax_years,
    TAX_JOURNAL_SQL,
    upsert_raw_material_price, upsert_raw_material_summary,
    load_raw_material_price, RAW_MATERIAL_SQL,
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

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📤 분개장 업로드",
    "📊 세무사 분개장 (감가상각)",
    "🚫 블랙리스트",
    "🏦 대출 정보",
    "🪟 원판 데이터 업로드",
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

# ── TAB 5: 원판 데이터 업로드 ─────────────────────────────────────────────
with tab5:
    st.subheader("원판 구매내역 · 수불부 업로드")
    st.info(
        "**파일 1 (구매내역)**: `원판구매내역_Yr{YYYY}-{MM}-{DD}.xlsx` — 거래처별 시트 구분\n\n"
        "**파일 2 (수불부)**: `{YYYY}_{MM}_원판수불부_곡성.xlsx` — 기초/입고/사용/기말 재고\n\n"
        "파일명에서 연월을 자동 추출합니다. 같은 연월 재업로드 시 덮어씁니다."
    )

    with st.expander("⚙️ 최초 설정: Supabase raw_material 테이블 생성"):
        st.caption("Supabase 대시보드 → SQL Editor에서 실행 (최초 1회)")
        st.code(RAW_MATERIAL_SQL, language="sql")

    st.divider()

    # ── 파일 1: 구매내역 ─────────────────────────────────────────────────
    st.markdown("#### 📥 파일 1: 원판 구매내역")
    st.caption("시트탭명 = 거래처명 (KCC글라스, LX글라스, 한유에스앤지, 한성유엔씨)")

    price_file = st.file_uploader(
        "원판구매내역 파일 (xlsx)",
        type=["xlsx", "xls"],
        key="rawmat_price_upload",
    )

    def _extract_ym_from_filename(name: str) -> tuple[str, str]:
        """파일명에서 (year, month) 추출. 실패 시 ("", "")."""
        import re
        m = re.search(r"(\d{4})[_\-](\d{2})", name)
        if m:
            return m.group(1), m.group(2)
        m = re.search(r"Yr(\d{4})[_\-](\d{2})", name)
        if m:
            return m.group(1), m.group(2)
        return "", ""

    def _safe_float(val, default=0.0) -> float:
        """NaN 포함 모든 값을 안전하게 float로 변환."""
        if val is None:
            return default
        try:
            f = float(val)
            return default if (f != f) else f  # NaN check: NaN != NaN
        except (ValueError, TypeError):
            return default

    def _safe_int(val) -> int | None:
        """NaN/None/비정상값을 None으로, 정상값만 int로 변환."""
        f = _safe_float(val, float("nan"))
        if f != f:  # NaN
            return None
        try:
            return int(f)
        except (ValueError, TypeError):
            return None

    def _calc_unit_price(row) -> dict:
        """단가 계산: 면적(m²) 기반 원/㎡, 원/평."""
        try:
            w = _safe_float(row.get("가로") or row.get("폭"))
            h = _safe_float(row.get("세로") or row.get("길이"))
            qty = _safe_float(row.get("매수") or row.get("수량"), 1.0)
            amt = _safe_float(row.get("금액") or row.get("공급가액"))

            # mm → m 변환 (10 초과면 mm 단위로 가정)
            if w > 10:
                w /= 1000
            if h > 10:
                h /= 1000

            area = w * h * qty
            if area <= 0 or amt <= 0:
                return {"면적_m2": 0, "금액_원": amt, "원_m2": 0, "원_평": 0}

            won_m2 = amt / area
            won_pyong = round(won_m2 / 10.764)
            return {"면적_m2": round(area, 4), "금액_원": amt,
                    "원_m2": round(won_m2), "원_평": won_pyong}
        except Exception:
            return {"면적_m2": 0, "금액_원": 0, "원_m2": 0, "원_평": 0}

    if price_file:
        fname = price_file.name
        auto_year, auto_month = _extract_ym_from_filename(fname)

        col_y, col_m = st.columns(2)
        inp_year = col_y.text_input("연도 (자동감지)", value=auto_year)
        inp_month = col_m.text_input("월 (자동감지)", value=auto_month)

        try:
            xl = pd.ExcelFile(price_file)
            sheets = xl.sheet_names
            st.success(f"파일 읽기 완료 — 시트 {len(sheets)}개: {', '.join(sheets)}")

            all_price_rows = []
            for sheet in sheets:
                try:
                    df_s = xl.parse(sheet, header=None)
                    # 헤더 행 탐색 (가로/폭/세로 등 컬럼명 포함 행)
                    header_row = None
                    for i, row in df_s.iterrows():
                        row_str = " ".join(str(v) for v in row.values if pd.notna(v))
                        if any(k in row_str for k in ["가로", "세로", "폭", "길이", "두께", "매수", "금액"]):
                            header_row = i
                            break
                    if header_row is not None:
                        df_s.columns = df_s.iloc[header_row]
                        df_s = df_s.iloc[header_row+1:].reset_index(drop=True)
                        df_s = df_s.dropna(how="all")

                    # 원산지 필터링 (원산지 문자열 포함 행 제외)
                    if "원산지" in df_s.columns:
                        df_s = df_s[~df_s["원산지"].astype(str).str.contains("원산지", na=False)]

                    # 거래처 시트에 해당하는 것만 처리 (Summary/수불용/기준 시트 제외)
                    SKIP_SHEETS = {"summary", "summay", "수불용", "기준"}
                    if sheet.lower().replace("_", "").replace(" ", "") in SKIP_SHEETS or \
                       any(k in sheet.lower() for k in ["summary", "summay", "수불", "기준"]):
                        continue

                    # 단가 계산
                    for _, r in df_s.iterrows():
                        price_calc = _calc_unit_price(r)
                        row_data = {
                            "year": inp_year, "month": inp_month,
                            "거래처": sheet,
                            "원산지": str(r.get("원산지", "") or "").strip() if "원산지" in df_s.columns else "",
                            "제품": str(r.get("제품") or r.get("품명") or "").strip(),
                            "두께": _safe_int(r.get("두께")),
                            "규격mm": str(r.get("규격") or r.get("규격(mm)") or "").strip(),
                            "규격자": str(r.get("규격(자)") or r.get("자수") or "").strip(),
                            "일자": str(r.get("일자") or r.get("날짜") or "").strip(),
                        }
                        row_data.update(price_calc)
                        row_data["파일_원_m2"] = _safe_float(r.get("원/m2") or r.get("단가"))
                        row_data["파일_원_평"] = _safe_float(r.get("원/평"))
                        row_data["오기여부"] = (
                            abs(row_data["원_m2"] - row_data["파일_원_m2"]) > 5
                            if row_data["파일_원_m2"] > 0 and row_data["원_m2"] > 0 else False
                        )
                        if price_calc["금액_원"] > 0:
                            all_price_rows.append(row_data)

                except Exception as e:
                    st.warning(f"시트 '{sheet}' 처리 실패: {e}")

            if all_price_rows:
                price_preview = pd.DataFrame(all_price_rows)
                st.markdown(f"**파싱 결과: {len(price_preview):,}건**")

                오기_cnt = price_preview["오기여부"].sum() if "오기여부" in price_preview.columns else 0
                if 오기_cnt > 0:
                    st.warning(f"⚠️ 오기 의심 {오기_cnt}건 — 파일 단가 vs 직접계산 ±5원 초과")

                with st.expander("미리보기 (상위 20행)"):
                    show = ["거래처", "두께", "규격자", "일자", "금액_원", "원_m2", "원_평"]
                    st.dataframe(price_preview[[c for c in show if c in price_preview.columns]].head(20),
                                 use_container_width=True, hide_index=True)

                if st.button("☁️ raw_material_price에 저장", type="primary", use_container_width=True, key="save_price"):
                    with st.spinner("저장 중..."):
                        try:
                            count = upsert_raw_material_price(price_preview, inp_year, inp_month)
                            st.success(f"✅ {inp_year}-{inp_month} 구매내역 {count:,}건 저장 완료!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"저장 실패: {e}")
                            st.info("Supabase SQL Editor에서 raw_material 테이블 생성 후 재시도하세요.")
            else:
                st.warning("파싱된 유효 데이터 없음 — 파일 컬럼 구조를 확인하세요.")

        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")

    st.divider()

    # ── 파일 2: 수불부 ───────────────────────────────────────────────────
    st.markdown("#### 📥 파일 2: 원판 수불부")
    st.caption("기초재고 / 당기입고 / 당기사용 / 기말재고 집계")

    summary_file = st.file_uploader(
        "원판 수불부 파일 (xlsx)",
        type=["xlsx", "xls"],
        key="rawmat_summary_upload",
    )

    if summary_file:
        fname2 = summary_file.name
        auto_year2, auto_month2 = _extract_ym_from_filename(fname2)

        col_y2, col_m2 = st.columns(2)
        inp_year2 = col_y2.text_input("연도", value=auto_year2, key="sy2")
        inp_month2 = col_m2.text_input("월", value=auto_month2, key="sm2")

        try:
            xl2 = pd.ExcelFile(summary_file)
            sheets2 = xl2.sheet_names
            st.success(f"파일 읽기 완료 — 시트 {len(sheets2)}개: {', '.join(sheets2)}")

            # ── 자동 집계 시도 ────────────────────────────────────────────
            # 구분 컬럼에서 기초/입고/출고/기말 행을 찾아 매수·금액 합산
            auto = {"기초재고_매": 0, "기초재고_금액": 0,
                    "당기입고_매": 0, "당기입고_금액": 0,
                    "당기사용_매": 0, "당기사용_금액": 0,
                    "기말재고_매": 0, "기말재고_금액": 0}
            parsed_ok = False

            # "수불용" 시트 우선, 없으면 전체 시트 순서
            priority = [s for s in sheets2 if "수불" in s] + \
                       [s for s in sheets2 if "수불" not in s]

            for sh in priority:
                try:
                    df_sh = xl2.parse(sh, header=None)
                    if df_sh.empty:
                        continue

                    # 헤더 행 탐색 (구분/매수/금액 등 포함 행)
                    hrow = None
                    for i, row in df_sh.iterrows():
                        rstr = " ".join(str(v) for v in row.values if pd.notna(v))
                        if any(k in rstr for k in ["매수", "금액", "수량", "구분"]):
                            hrow = i
                            break

                    if hrow is not None:
                        df_sh.columns = df_sh.iloc[hrow]
                        df_sh = df_sh.iloc[hrow+1:].reset_index(drop=True)

                    # 구분 컬럼 찾기 (구분, 항목, 품목 등)
                    구분col = next((c for c in df_sh.columns
                                   if str(c).strip() in ["구분","항목","품목","내용"]), None)
                    매수col = next((c for c in df_sh.columns
                                   if str(c).strip() in ["매수","수량","장수","매"]), None)
                    금액col = next((c for c in df_sh.columns
                                   if str(c).strip() in ["금액","원가","합계금액","금액(원)"]), None)

                    if 구분col is None:
                        # 구분 컬럼 없으면 전체 셀에서 키워드 탐색
                        for _, row in df_sh.iterrows():
                            for col_idx, val in enumerate(row.values):
                                val_str = str(val).strip()
                                if any(k in val_str for k in ["기초재고","전월재고","기초"]):
                                    # 같은 행에서 숫자 추출
                                    nums = [_safe_float(v) for v in row.values if _safe_float(v) > 0]
                                    if len(nums) >= 2:
                                        auto["기초재고_매"] += int(nums[0])
                                        auto["기초재고_금액"] += nums[1]
                                        parsed_ok = True
                                elif any(k in val_str for k in ["당기입고","입고"]):
                                    nums = [_safe_float(v) for v in row.values if _safe_float(v) > 0]
                                    if len(nums) >= 2:
                                        auto["당기입고_매"] += int(nums[0])
                                        auto["당기입고_금액"] += nums[1]
                                        parsed_ok = True
                                elif any(k in val_str for k in ["당기출고","당기사용","출고","사용"]):
                                    nums = [_safe_float(v) for v in row.values if _safe_float(v) > 0]
                                    if len(nums) >= 2:
                                        auto["당기사용_매"] += int(nums[0])
                                        auto["당기사용_금액"] += nums[1]
                                        parsed_ok = True
                                elif any(k in val_str for k in ["기말재고","기말"]):
                                    nums = [_safe_float(v) for v in row.values if _safe_float(v) > 0]
                                    if len(nums) >= 2:
                                        auto["기말재고_매"] += int(nums[0])
                                        auto["기말재고_금액"] += nums[1]
                                        parsed_ok = True
                    else:
                        KEYWORD_MAP = {
                            "기초": ("기초재고_매", "기초재고_금액"),
                            "기초재고": ("기초재고_매", "기초재고_금액"),
                            "전월재고": ("기초재고_매", "기초재고_금액"),
                            "입고": ("당기입고_매", "당기입고_금액"),
                            "당기입고": ("당기입고_매", "당기입고_금액"),
                            "출고": ("당기사용_매", "당기사용_금액"),
                            "사용": ("당기사용_매", "당기사용_금액"),
                            "당기출고": ("당기사용_매", "당기사용_금액"),
                            "당기사용": ("당기사용_매", "당기사용_금액"),
                            "기말": ("기말재고_매", "기말재고_금액"),
                            "기말재고": ("기말재고_매", "기말재고_금액"),
                        }
                        for _, row in df_sh.iterrows():
                            구분val = str(row.get(구분col, "")).strip()
                            for kw, (매_key, 금액_key) in KEYWORD_MAP.items():
                                if kw in 구분val:
                                    if 매수col:
                                        auto[매_key] += _safe_int(row.get(매수col)) or 0
                                    if 금액col:
                                        auto[금액_key] += _safe_float(row.get(금액col))
                                    parsed_ok = True
                                    break

                    if parsed_ok:
                        break  # 첫 번째 파싱 성공 시트에서 중단

                except Exception:
                    continue

            # ── 결과 표시 ────────────────────────────────────────────────
            if parsed_ok:
                st.success("✅ 파일에서 집계값 자동 추출 완료 — 확인 후 저장하세요.")
            else:
                st.warning("⚠️ 자동 추출 실패 — 아래에 직접 입력해주세요.")

            # 파싱 결과를 기본값으로 form 표시
            with st.expander("📋 파일 원시 데이터 확인 (첫 번째 시트)", expanded=False):
                try:
                    preview_df = xl2.parse(priority[0], header=None, nrows=20)
                    st.dataframe(preview_df, use_container_width=True)
                except Exception:
                    pass

            st.markdown("**수불 집계 확인 / 수정**")
            c1, c2 = st.columns(2)
            with c1:
                ki_mae = st.number_input("기초재고 (매)", min_value=0, step=1,
                                         value=int(auto["기초재고_매"]), key="ki_mae")
                ki_amt = st.number_input("기초재고 (금액, 원)", min_value=0, step=1000,
                                          value=int(auto["기초재고_금액"]), key="ki_amt")
                in_mae = st.number_input("당기입고 (매)", min_value=0, step=1,
                                          value=int(auto["당기입고_매"]), key="in_mae")
                in_amt = st.number_input("당기입고 (금액, 원)", min_value=0, step=1000,
                                          value=int(auto["당기입고_금액"]), key="in_amt")
            with c2:
                use_mae = st.number_input("당기사용 (매)", min_value=0, step=1,
                                           value=int(auto["당기사용_매"]), key="use_mae")
                use_amt = st.number_input("당기사용 (금액, 원)", min_value=0, step=1000,
                                           value=int(auto["당기사용_금액"]), key="use_amt")
                ke_mae = st.number_input("기말재고 (매)", min_value=0, step=1,
                                          value=int(auto["기말재고_매"]), key="ke_mae")
                ke_amt = st.number_input("기말재고 (금액, 원)", min_value=0, step=1000,
                                          value=int(auto["기말재고_금액"]), key="ke_amt")

            if st.button("☁️ raw_material_summary에 저장", type="primary", use_container_width=True, key="save_summary"):
                with st.spinner("저장 중..."):
                    try:
                        summary_data = {
                            "기초재고_매": ki_mae, "기초재고_금액": ki_amt,
                            "당기입고_매": in_mae, "당기입고_금액": in_amt,
                            "당기사용_매": use_mae, "당기사용_금액": use_amt,
                            "기말재고_매": ke_mae, "기말재고_금액": ke_amt,
                        }
                        upsert_raw_material_summary(summary_data, inp_year2, inp_month2)
                        st.success(f"✅ {inp_year2}-{inp_month2} 수불 집계 저장 완료!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")
        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")
