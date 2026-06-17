import streamlit as st
from supabase import create_client, Client
import pandas as pd


@st.cache_resource
def get_client() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["service_role_key"]
    return create_client(url, key)


JOURNAL_COLUMNS = [
    "ym", "전표일자", "전표번호", "구분", "계정코드", "계정과목",
    "차변", "대변", "거래처", "거래처코드", "적요", "계정그룹",
]


def upsert_journal(df: pd.DataFrame, ym: str) -> int:
    """월별 분개 데이터를 Supabase에 저장 (같은 달 재업로드 시 덮어쓰기)."""
    client = get_client()
    # Supabase 테이블에 있는 컬럼만 추려서 전송
    send_cols = [c for c in JOURNAL_COLUMNS if c in df.columns]
    df_send = df[send_cols].copy()
    # 기존 데이터 삭제
    client.table("journal").delete().eq("ym", ym).execute()
    # 새 데이터 삽입 (500행 배치)
    records = df_send.to_dict(orient="records")
    batch = 500
    for i in range(0, len(records), batch):
        client.table("journal").insert(records[i:i+batch]).execute()
    # 성공 시 monthly_summary에 해당 월 등록 (get_available_months용)
    client.table("monthly_summary").upsert({"ym": ym}).execute()
    return len(records)


def load_journal(ym: str) -> pd.DataFrame:
    """특정 월 분개 데이터 로드 (페이지네이션 — 월 2,000행+ 대응)."""
    client = get_client()
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        res = (client.table("journal")
               .select("*")
               .eq("ym", ym)
               .range(offset, offset + page_size - 1)
               .execute())
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < page_size:
            break
        offset += page_size
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["전표일자"] = pd.to_datetime(df["전표일자"])
    return df


def load_journal_code(code: str) -> pd.DataFrame:
    """특정 계정코드 전체 이력 로드 — load_all_journal 대신 사용 (속도 개선).

    Code 931(이자비용) 등 특정 계정만 필요할 때 전체 분개 대신 이 함수를 사용.
    """
    client = get_client()
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        res = (client.table("journal")
               .select("ym,전표일자,차변,대변,거래처,적요")
               .eq("계정코드", code)
               .order("전표일자")
               .range(offset, offset + page_size - 1)
               .execute())
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < page_size:
            break
        offset += page_size
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["전표일자"] = pd.to_datetime(df["전표일자"])
    for col in ["차변", "대변"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def load_all_journal() -> pd.DataFrame:
    """전체 분개 데이터 로드 (페이지네이션)."""
    client = get_client()
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        res = (client.table("journal")
               .select("*")
               .order("전표일자")
               .range(offset, offset + page_size - 1)
               .execute())
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < page_size:
            break
        offset += page_size
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["전표일자"] = pd.to_datetime(df["전표일자"])
    return df


def load_journal_upto(ym: str) -> pd.DataFrame:
    """선택월 말일까지 전체 누적 분개 데이터 로드 (AR aging용).

    108+110 계정만 로드해 전체 분개 대비 ~5% 크기로 속도·안정성 개선.
    매출채권 FIFO / 어음수취 판별에 필요한 계정만 포함.
    """
    import calendar
    year, month = int(ym[:4]), int(ym[5:7])
    last_day = calendar.monthrange(year, month)[1]
    end_date = f"{ym}-{last_day:02d}"

    client = get_client()
    all_data = []
    page_size = 1000

    for code_prefix in ["108", "110"]:
        offset = 0
        while True:
            try:
                res = (client.table("journal")
                       .select("*")
                       .like("계정코드", f"{code_prefix}%")
                       .lte("전표일자", end_date)
                       .order("전표일자")
                       .range(offset, offset + page_size - 1)
                       .execute())
            except Exception:
                # HTTP/2 일시 오류 시 1회 재시도
                try:
                    res = (client.table("journal")
                           .select("*")
                           .like("계정코드", f"{code_prefix}%")
                           .lte("전표일자", end_date)
                           .order("전표일자")
                           .range(offset, offset + page_size - 1)
                           .execute())
                except Exception:
                    break
            if not res.data:
                break
            all_data.extend(res.data)
            if len(res.data) < page_size:
                break
            offset += page_size

    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["전표일자"] = pd.to_datetime(df["전표일자"])
    return df


def get_available_months() -> list[str]:
    """저장된 월 목록 반환 (YYYY-MM 정렬).

    monthly_summary 테이블에서 가져옴 (행 수가 적어 1000행 한계 없음).
    journal 테이블에 직접 조회하면 수만 행 한계에 걸려 일부 달만 보임.
    """
    client = get_client()
    res = client.table("monthly_summary").select("ym").execute()
    if not res.data:
        return []
    months = sorted(set(r["ym"] for r in res.data), reverse=True)
    return months


def upsert_monthly_summary(summary: dict) -> None:
    client = get_client()
    client.table("monthly_summary").upsert(summary).execute()


def load_monthly_summary() -> pd.DataFrame:
    client = get_client()
    res = client.table("monthly_summary").select("*").order("ym").execute()
    if not res.data:
        return pd.DataFrame()
    return pd.DataFrame(res.data)


def is_erp_ar_verified() -> bool:
    """ERP 미수금 대조 완료 여부 반환."""
    client = get_client()
    try:
        res = (client.table("master_settings")
               .select("key")
               .eq("type", "erp_verified")
               .eq("key", "ar")
               .execute())
        return bool(res.data)
    except Exception:
        return False


def set_erp_ar_verified(verified: bool) -> None:
    """ERP 미수금 대조 완료 플래그 설정/해제."""
    client = get_client()
    try:
        if verified:
            client.table("master_settings").upsert(
                {"type": "erp_verified", "key": "ar"}
            ).execute()
        else:
            client.table("master_settings").delete().eq("type", "erp_verified").eq("key", "ar").execute()
    except Exception:
        pass


def load_master_blacklist() -> list[str]:
    client = get_client()
    res = (client.table("master_settings")
           .select("key")
           .eq("type", "blacklist")
           .execute())
    return [r["key"] for r in res.data] if res.data else []


def add_blacklist(name: str) -> None:
    client = get_client()
    client.table("master_settings").upsert(
        {"type": "blacklist", "key": name}
    ).execute()


def load_debts() -> pd.DataFrame:
    client = get_client()
    res = client.table("debts").select("*").order("은행명").execute()
    if not res.data:
        return pd.DataFrame()
    return pd.DataFrame(res.data)


# ── tax_journal (세무사 분개장 — 감가상각 전용) ──────────────────────────────

TAX_JOURNAL_COLUMNS = [
    "year", "전표일자", "전표번호", "계정코드", "계정과목",
    "차변", "대변", "거래처", "적요",
]

TAX_JOURNAL_SQL = """
-- Supabase SQL Editor에서 실행
CREATE TABLE IF NOT EXISTS tax_journal (
    id          bigserial PRIMARY KEY,
    year        text NOT NULL,
    전표일자    text,
    전표번호    text,
    계정코드    text,
    계정과목    text,
    차변        numeric DEFAULT 0,
    대변        numeric DEFAULT 0,
    거래처      text,
    적요        text
);
CREATE INDEX IF NOT EXISTS idx_tax_journal_year ON tax_journal(year);
CREATE INDEX IF NOT EXISTS idx_tax_journal_code ON tax_journal(계정코드);
"""


def upsert_tax_journal(df: pd.DataFrame, year: str) -> int:
    """세무사 분개장을 tax_journal 테이블에 저장 (연도 단위 덮어쓰기)."""
    client = get_client()
    try:
        client.table("tax_journal").delete().eq("year", year).execute()
        send_cols = [c for c in TAX_JOURNAL_COLUMNS if c in df.columns]
        records = df[send_cols].to_dict(orient="records")
        batch = 500
        for i in range(0, len(records), batch):
            client.table("tax_journal").insert(records[i:i+batch]).execute()
        return len(records)
    except Exception as e:
        raise RuntimeError(f"tax_journal 저장 실패: {e}")


def load_tax_depreciation() -> pd.DataFrame:
    """tax_journal에서 감가상각 계정(518·818·840) 전체 이력 조회."""
    client = get_client()
    try:
        res = (client.table("tax_journal")
               .select("year,전표일자,계정코드,계정과목,차변")
               .in_("계정코드", ["518", "818", "840"])
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df["차변"] = pd.to_numeric(df["차변"], errors="coerce").fillna(0)
        return df
    except Exception:
        return pd.DataFrame()


def get_tax_depreciation_annual(year: str) -> float:
    """특정 연도 세무사 분개장 기준 연간 감가상각 총액."""
    dep_df = load_tax_depreciation()
    if dep_df.empty:
        return 0.0
    year_data = dep_df[dep_df["year"].astype(str) == str(year)]
    return float(year_data["차변"].sum())


def get_annual_dep(year: str) -> float:
    """연간 감가상각 총액 자동 추출.

    우선순위:
    1. tax_journal (세무사 분개장 업로드 시 — 2022~2024 소급 가능)
    2. 직원 분개장 12월 Code 518/818/840 차변 (2025년부터 자동 인식)
    """
    # 1. tax_journal 우선
    dep = get_tax_depreciation_annual(year)
    if dep > 0:
        return dep
    # 2. 직원 분개장 12월에서 자동 추출
    try:
        df = load_journal(f"{year}-12")
        if df.empty:
            return 0.0
        codes = ["518", "818", "840"]
        dep_rows = df[df["계정코드"].astype(str).isin(codes)]
        return float(dep_rows["차변"].sum())
    except Exception:
        return 0.0


def get_tax_years() -> list[str]:
    """tax_journal에 업로드된 연도 목록 반환."""
    client = get_client()
    try:
        res = client.table("tax_journal").select("year").execute()
        if not res.data:
            return []
        return sorted(set(r["year"] for r in res.data), reverse=True)
    except Exception:
        return []


# ── raw_material (원판 단가·수불 — 원판관리 페이지) ─────────────────────────

RAW_MATERIAL_SQL = """
-- Supabase SQL Editor에서 실행 (최초 1회)
CREATE TABLE IF NOT EXISTS raw_material_price (
    id          bigserial PRIMARY KEY,
    year        text NOT NULL,
    month       text NOT NULL,
    거래처      text,
    원산지      text,
    제품        text,
    두께        int,
    규격mm      text,
    규격자      text,
    일자        text,
    면적_m2     numeric DEFAULT 0,
    금액_원     numeric DEFAULT 0,
    부가세_원   numeric DEFAULT 0,
    합계_원     numeric DEFAULT 0,
    원_m2       numeric DEFAULT 0,
    원_평       numeric DEFAULT 0,
    파일_원_m2  numeric DEFAULT 0,
    파일_원_평  numeric DEFAULT 0,
    오기여부    boolean DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_rmp_ym ON raw_material_price(year, month);
-- 기존 테이블에 컬럼 추가 (이미 생성된 경우):
-- ALTER TABLE raw_material_price ADD COLUMN IF NOT EXISTS 부가세_원 numeric DEFAULT 0;
-- ALTER TABLE raw_material_price ADD COLUMN IF NOT EXISTS 합계_원 numeric DEFAULT 0;

CREATE TABLE IF NOT EXISTS raw_material_summary (
    id              bigserial PRIMARY KEY,
    year            text NOT NULL,
    month           text NOT NULL,
    당기입고_매     int DEFAULT 0,
    당기입고_금액   numeric DEFAULT 0,
    당기사용_매     int DEFAULT 0,
    당기사용_금액   numeric DEFAULT 0,
    기초재고_매     int DEFAULT 0,
    기초재고_금액   numeric DEFAULT 0,
    기말재고_매     int DEFAULT 0,
    기말재고_금액   numeric DEFAULT 0,
    UNIQUE(year, month)
);
"""

RAW_MATERIAL_PRICE_COLUMNS = [
    "year", "month", "거래처", "원산지", "제품", "두께", "규격mm", "규격자",
    "일자", "면적_m2", "금액_원", "부가세_원", "합계_원",
    "원_m2", "원_평", "파일_원_m2", "파일_원_평", "오기여부",
]


def upsert_raw_material_price(df: pd.DataFrame, year: str, month: str) -> int:
    """원판 단가 데이터를 raw_material_price에 저장 (연월 단위 덮어쓰기)."""
    import json, re

    def _to_safe_records(records):
        """NaN/Infinity/pd.NA 등 JSON 불가 값을 null로 치환."""
        # allow_nan=True: NaN → 문자열 "NaN", default=str: 나머지 불가 타입 → 문자열
        s = json.dumps(records, allow_nan=True, default=str)
        # NaN / Infinity 를 null로 교체
        s = re.sub(r'\b(NaN|Infinity|-Infinity)\b', 'null', s)
        return json.loads(s)

    client = get_client()
    try:
        client.table("raw_material_price").delete().eq("year", year).eq("month", month).execute()
        send_cols = [c for c in RAW_MATERIAL_PRICE_COLUMNS if c in df.columns]
        raw_records = df[send_cols].to_dict(orient="records")
        records = _to_safe_records(raw_records)
        batch = 500
        for i in range(0, len(records), batch):
            client.table("raw_material_price").insert(records[i:i+batch]).execute()
        return len(records)
    except Exception as e:
        raise RuntimeError(f"raw_material_price 저장 실패: {e}")


def load_raw_material_price(year: str, month: str) -> pd.DataFrame:
    """특정 연월 원판 단가 데이터 로드."""
    client = get_client()
    try:
        res = (client.table("raw_material_price")
               .select("*")
               .eq("year", year)
               .eq("month", month)
               .execute())
        if not res.data:
            return pd.DataFrame()
        return pd.DataFrame(res.data)
    except Exception:
        return pd.DataFrame()


def upsert_raw_material_summary(data: dict, year: str, month: str) -> None:
    """원판 월별 수불 집계 저장 (같은 연월 덮어쓰기)."""
    client = get_client()
    try:
        client.table("raw_material_summary").delete().eq("year", year).eq("month", month).execute()
        data_with_ym = {**data, "year": str(year), "month": str(month)}
        client.table("raw_material_summary").insert(data_with_ym).execute()
    except Exception as e:
        raise RuntimeError(f"raw_material_summary 저장 실패: {e}")


def load_raw_material_summary() -> pd.DataFrame:
    """전체 원판 월별 수불 집계 로드."""
    client = get_client()
    try:
        res = (client.table("raw_material_summary")
               .select("*")
               .order("year")
               .order("month")
               .execute())
        if not res.data:
            return pd.DataFrame()
        return pd.DataFrame(res.data)
    except Exception:
        return pd.DataFrame()


def get_raw_material_months() -> list[tuple[str, str]]:
    """raw_material_price에 저장된 (연도, 월) 목록 반환."""
    client = get_client()
    try:
        res = client.table("raw_material_price").select("year,month").execute()
        if not res.data:
            return []
        pairs = sorted(set((r["year"], r["month"]) for r in res.data), reverse=True)
        return pairs
    except Exception:
        return []
