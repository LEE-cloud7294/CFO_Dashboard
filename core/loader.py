import pandas as pd
import io


def load_excel(file) -> pd.DataFrame:
    """엑셀 또는 CSV 파일을 DataFrame으로 읽기."""
    if hasattr(file, "name"):
        name = file.name.lower()
    else:
        name = str(file).lower()

    if name.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(file, engine="calamine", dtype=str)
        except Exception:
            df = pd.read_excel(file, dtype=str)
    else:
        raw = file.read() if hasattr(file, "read") else open(file, "rb").read()
        try:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="cp949")
    return df


def clean_journal(df: pd.DataFrame) -> pd.DataFrame:
    """분개장 DataFrame 정제.

    - 컬럼명 표준화
    - 요약행(날짜 없는 행) 제거
    - 차변/대변 숫자 변환
    - 계정그룹 컬럼 추가
    """
    df = df.copy()

    # 컬럼명 정리: 공백 제거
    df.columns = [str(c).strip() for c in df.columns]

    # 필수 컬럼 확인 및 이름 맞추기
    col_map = {}
    for col in df.columns:
        if "전표일자" in col:
            col_map[col] = "전표일자"
        elif "전표번호" in col:
            col_map[col] = "전표번호"
        elif col == "Code" or col == "code":
            col_map[col] = "계정코드"
        elif "계정과목" in col:
            col_map[col] = "계정과목"
        elif col == "차변":
            col_map[col] = "차변"
        elif col == "대변":
            col_map[col] = "대변"
        elif "거래처" in col and "코드" not in col and "Code" not in col:
            col_map[col] = "거래처"
        elif "적요" in col:
            col_map[col] = "적요"
        elif "구분" in col:
            col_map[col] = "구분"
    df = df.rename(columns=col_map)

    # 거래처코드: Code.1 또는 Code(거래처코드)
    for col in df.columns:
        if col == "Code.1" or ("Code" in col and col != "계정코드"):
            df = df.rename(columns={col: "거래처코드"})
            break

    # 요약행 제거: 전표일자가 유효한 날짜인 행만 유지
    df["전표일자"] = pd.to_datetime(df.get("전표일자", pd.Series()), errors="coerce")
    df = df[df["전표일자"].notna()].copy()

    # 차변/대변 숫자 변환
    for col in ["차변", "대변"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ""), errors="coerce"
            ).fillna(0)
        else:
            df[col] = 0.0

    # 계정코드 문자열 정리
    df["계정코드"] = df.get("계정코드", "").astype(str).str.strip()

    # 계정그룹: 첫 자리 기준
    df["계정그룹"] = df["계정코드"].str[:1]

    # 거래처 NaN → 빈 문자열
    for col in ["거래처", "거래처코드", "적요", "계정과목", "전표번호", "구분"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
        else:
            df[col] = ""

    # 필요한 컬럼만 반환
    keep = ["전표일자", "전표번호", "구분", "계정코드", "계정과목",
            "차변", "대변", "거래처", "거래처코드", "적요", "계정그룹"]
    df = df[[c for c in keep if c in df.columns]]

    # 날짜를 문자열 ISO 포맷으로 (Supabase 저장용)
    df["전표일자"] = df["전표일자"].dt.strftime("%Y-%m-%d")

    return df.reset_index(drop=True)


def get_ym(df: pd.DataFrame) -> str:
    """DataFrame에서 YYYY-MM 연월 자동 감지."""
    dates = pd.to_datetime(df["전표일자"], errors="coerce").dropna()
    if dates.empty:
        return "unknown"
    most_common = dates.dt.to_period("M").value_counts().index[0]
    return str(most_common)


def add_ym_column(df: pd.DataFrame, ym: str) -> pd.DataFrame:
    df = df.copy()
    df["ym"] = ym
    return df
