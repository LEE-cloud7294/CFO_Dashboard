import streamlit as st
from supabase import create_client, Client
import pandas as pd


@st.cache_resource
def get_client() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["anon_key"]
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
    """선택월 말일까지의 전체 누적 분개 데이터 로드 (AR aging용).

    매출채권 잔액은 창업일부터 선택월 말일까지의 모든 108 계정 입출을
    누적해야 정확하게 계산된다.
    """
    import calendar
    year, month = int(ym[:4]), int(ym[5:7])
    last_day = calendar.monthrange(year, month)[1]
    end_date = f"{ym}-{last_day:02d}"

    client = get_client()
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        res = (client.table("journal")
               .select("*")
               .lte("전표일자", end_date)
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
