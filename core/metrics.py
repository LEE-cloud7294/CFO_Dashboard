import pandas as pd
from typing import Optional
import calendar


def fmt_krw(v: float, suffix: str = "원") -> str:
    """금액을 원 단위 천단위 쉼표로 표시. 예) 857,600,000원"""
    return f"{round(v):,}{suffix}"


def apply_monthly_depreciation(kpi: dict, monthly_dep: float) -> dict:
    """kpi dict에 월별 감가상각 적용 (항상 ÷12 균등 배분).

    - 연말 일괄 감가상각(자산처분손실)을 제거하고 monthly_dep으로 대체
    - 이중계산 방지
    """
    if monthly_dep <= 0:
        return kpi
    kpi = dict(kpi)
    kpi["영업이익_v7"] = kpi.get("영업이익_v7", 0) - monthly_dep
    매출 = kpi.get("매출액", 0)
    kpi["영업이익률_v7"] = round(kpi["영업이익_v7"] / 매출 * 100, 2) if 매출 > 0 else 0
    kpi["자산처분손실"] = 0  # 직원분개장 연말 덩어리 제거 (월별 배분으로 대체)
    kpi["감가상각_월"] = monthly_dep
    kpi["실질이익"] = (
        kpi["영업이익_v7"]
        + kpi.get("영업외수익", 0)
        - kpi.get("이자비용", 0)
    )
    return kpi


def cost_bucket(code, name, note: str = "") -> Optional[str]:
    """비용 계정을 대분류로 분류 (CLAUDE.md §7 완전 구현).

    Returns None for 감가상각누계 (비용 아님 — 자산처분 상계항목).
    """
    n = str(name)
    note = str(note)
    try:
        c = int(float(str(code))) if str(code).replace(".", "").isdigit() else 0
    except (ValueError, TypeError):
        c = 0

    # ── 원재료·부재료 (코드 또는 계정명 기준) ────────────────────────────────
    # Code 153: 원재료 재고계정 / Code 501: 원재료비(제) 등 비용계정 모두 포함
    if c == 153 or any(k in n for k in ["원재료비", "원재료"]):
        return "원재료"
    # Code 162: 부재료 재고계정 / Code 502: 부재료비(제) 등
    if c == 162 or any(k in n for k in ["부재료비", "부재료"]):
        return "부재료"

    # ── 인건비 (실질) — 대광용역비 포함 ──────────────────────────────────────
    if any(k in n for k in ["급여", "임금", "상여", "퇴직", "잡급", "복리후생", "DC형"]):
        return "인건비"
    if "대광용역" in n:
        return "인건비"

    # ── 전력·수도 ──────────────────────────────────────────────────────────
    if any(k in n for k in ["전력", "가스수도", "수도료", "가스", "수도"]):
        return "전력·수도"

    # ── 안전관리비 ──────────────────────────────────────────────────────────
    if "안전관리비" in n:
        return "안전관리비"
    if "회사설정계정과목" in n:
        if any(k in note for k in ["안전", "마스크", "소방", "중대재해", "크레인", "방진", "보호구"]):
            return "안전관리비"
        return "유지·소모품"

    # ── 하드웨어 (소모공구비 = 판매용 재고를 소모품으로 계상) ───────────────
    if "소모공구비" in n:
        return "하드웨어"

    # ── 일회성손익 (감가상각비·자산처분 — 영업이익 아래 별도 표시) ─────────────
    if any(k in n for k in ["유형자산처분", "무형고정자산상각", "감가상각비"]):
        return "일회성손익"
    if "감가상각누계" in n:
        return None  # 상계항목 — 비용 아님

    # ── 대손상각 ──────────────────────────────────────────────────────────
    if "대손상각" in n:
        return "대손상각"

    # ── 물류·차량 ──────────────────────────────────────────────────────────
    if any(k in n for k in ["운반", "차량유지", "차량렌트", "여비교통"]):
        return "물류·차량"

    # ── 지급수수료 — 적요 기반 재분류 ────────────────────────────────────────
    if "지급수수료" in n:
        if any(k in note for k in ["조정료", "기장료", "수수료", "월정료", "연회비", "추심"]):
            return "수수료"
        if any(k in note for k in ["오니", "슬러지", "방제", "소방", "안전", "검사"]):
            return "안전관리비"
        if any(k in note for k in ["하치장", "중개", "운반", "물류"]):
            return "물류·차량"
        if any(k in note for k in ["시험", "인증", "심사", "연구"]):
            return "연구개발"
        return "수수료"

    # ── 카드수수료 ────────────────────────────────────────────────────────
    if "카드수수료" in n:
        return "수수료"

    # ── 세금·임차 ──────────────────────────────────────────────────────────
    if any(k in n for k in ["세금과공과", "임차료", "임차"]):
        return "세금·임차"

    # ── 유지·소모품 ──────────────────────────────────────────────────────────
    if any(k in n for k in ["소모품", "수선"]):
        return "유지·소모품"

    # ── 보험료 ──────────────────────────────────────────────────────────────
    if "보험" in n:
        return "보험료"

    # ── 연구개발 ──────────────────────────────────────────────────────────
    if "연구개발" in n:
        return "연구개발"

    # ── 판관비경비 ──────────────────────────────────────────────────────────
    if any(k in n for k in ["접대", "기업업무추진", "통신", "사무용품",
                             "도서인쇄", "교육훈련", "광고", "잡비"]):
        return "판관비경비"

    return "기타"


def _apply_cost_bucket(df: pd.DataFrame) -> pd.Series:
    """DataFrame 행별로 cost_bucket 적용 (code, name, note 전달)."""
    return df.apply(
        lambda r: cost_bucket(
            r.get("계정코드", ""),
            r.get("계정과목", ""),
            r.get("적요", ""),
        ),
        axis=1,
    )


def calc_kpi(df: pd.DataFrame) -> dict:
    """핵심 KPI 계산 (CLAUDE.md §5 기준)."""
    # 매출액: Code 404 대변 (반드시 404만 — §5 명시)
    매출액 = df[df["계정코드"].astype(str) == "404"]["대변"].sum()

    # 비용: 5xx + 8xx 차변 (감가상각누계 None 제외)
    cost_df = df[df["계정그룹"].isin(["5", "8"])].copy()
    cost_df["대분류"] = _apply_cost_bucket(cost_df)
    cost_df = cost_df[cost_df["대분류"].notna()]  # None(감가상각누계) 제거

    총비용 = cost_df["차변"].sum()
    cost_by_bucket = cost_df.groupby("대분류")["차변"].sum().to_dict()

    # 영업이익: 매출 - 전체 운영비용 (5xx+8xx)
    영업이익 = 매출액 - 총비용
    영업이익률 = (영업이익 / 매출액 * 100) if 매출액 > 0 else 0

    인건비 = cost_by_bucket.get("인건비", 0)
    인건비율 = (인건비 / 매출액 * 100) if 매출액 > 0 else 0

    원재료 = cost_by_bucket.get("원재료", 0)
    부재료 = cost_by_bucket.get("부재료", 0)
    원재료율 = ((원재료 + 부재료) / 매출액 * 100) if 매출액 > 0 else 0

    # 외상매출금(108) 잔액: 차변 - 대변
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")]
    외상매출금잔액 = ar_df["차변"].sum() - ar_df["대변"].sum()
    회전일수 = (외상매출금잔액 / 매출액 * 30) if 매출액 > 0 else 0

    # 손익분기 계산
    고정비 = (
        cost_by_bucket.get("인건비", 0)
        + cost_by_bucket.get("보험료", 0)
        + cost_df[cost_df["계정과목"].str.contains("임차", na=False)]["차변"].sum()
    )
    변동비율 = ((총비용 - 고정비) / 매출액) if 매출액 > 0 else 0
    손익분기매출 = (고정비 / (1 - 변동비율)) if (1 - 변동비율) > 0 else 0

    손익분기달성일 = None
    try:
        dates = pd.to_datetime(df["전표일자"], errors="coerce").dropna()
        if not dates.empty:
            ym = dates.dt.to_period("M").value_counts().index[0]
            days_in_month = calendar.monthrange(ym.year, ym.month)[1]
            ratio = 손익분기매출 / 매출액 if 매출액 > 0 else 1
            손익분기달성일 = max(1, min(int(ratio * days_in_month), days_in_month))
    except Exception:
        pass

    # ── §7 손익 구조 (원재료 현금매입 기준) ────────────────────────────────
    # Code 153: 월별 원재료 매입(자산) → 현금매입 기준 비용으로 처리
    원재료매입 = df[df["계정코드"].astype(str) == "153"]["차변"].sum()
    매입할인 = df[df["계정코드"].astype(str) == "155"]["대변"].sum()
    원재료순 = max(원재료매입 - 매입할인, 0)
    부재료매입 = df[df["계정코드"].astype(str) == "162"]["차변"].sum()

    # 5xx+8xx 중 원재료·부재료·일회성손익 제외
    # - 원재료·부재료: Code 153/162 기준 별도 계산
    # - 일회성손익(감가상각비·자산처분): 영업이익 아래 별도 표시
    EXCLUDE_FROM_영업 = ["원재료", "부재료", "일회성손익"]
    운영비_기타df = cost_df[~cost_df["대분류"].isin(EXCLUDE_FROM_영업)]
    운영비_기타 = 운영비_기타df["차변"].sum()
    운영비_기타bucket = 운영비_기타df.groupby("대분류")["차변"].sum().to_dict()

    # 일회성손익 (감가상각비·자산처분 — 영업이익 아래)
    자산처분손실 = cost_by_bucket.get("일회성손익", 0)

    영업이익_v7 = 매출액 - 원재료순 - 부재료매입 - 운영비_기타
    영업이익률_v7 = round((영업이익_v7 / 매출액 * 100) if 매출액 > 0 else 0, 2)

    # 이자비용 (Code 931)
    이자비용 = df[df["계정코드"].astype(str) == "931"]["차변"].sum()

    # 영업외수익
    # 901 이자수익: 반복성 / 914 유형자산처분이익·923 국고보조금·924 법인세환급: 일회성
    영업외수익_반복 = df[df["계정코드"].astype(str) == "901"]["대변"].sum()
    영업외수익_일회성 = df[df["계정코드"].astype(str).isin(["914", "923", "924"])]["대변"].sum()
    영업외수익 = 영업외수익_반복 + 영업외수익_일회성

    실질이익 = 영업이익_v7 + 영업외수익 - 이자비용 - 자산처분손실

    return {
        "매출액": 매출액,
        "총비용": 총비용,
        "운영비용": 총비용,
        "매입액": 0,
        "영업이익": 영업이익,
        "영업이익률": round(영업이익률, 2),
        # §7 방식 (Home 화면 P&L 표시용)
        "원재료매입": 원재료매입,
        "매입할인": 매입할인,
        "원재료순": 원재료순,
        "부재료매입": 부재료매입,
        "운영비_기타": 운영비_기타,
        "비용대분류_v7": 운영비_기타bucket,
        "영업이익_v7": 영업이익_v7,
        "영업이익률_v7": 영업이익률_v7,
        "이자비용": 이자비용,
        "자산처분손실": 자산처분손실,
        "영업외수익": 영업외수익,
        "영업외수익_반복": 영업외수익_반복,
        "영업외수익_일회성": 영업외수익_일회성,
        "실질이익": 실질이익,
        # 기존 필드 유지
        "인건비": 인건비,
        "인건비율": round(인건비율, 2),
        "원재료": 원재료,
        "부재료": 부재료,
        "원재료율": round(원재료율, 2),
        "외상매출금잔액": 외상매출금잔액,
        "매출채권회전일수": round(회전일수, 1),
        "고정비": 고정비,
        "손익분기매출": 손익분기매출,
        "손익분기달성일": 손익분기달성일,
        "비용대분류": cost_by_bucket,
    }


def calc_cost_detail(df: pd.DataFrame) -> pd.DataFrame:
    """비용 상세 데이터 (대분류 + 계정과목 + 금액)."""
    cost_df = df[df["계정그룹"].isin(["5", "8"])].copy()
    cost_df["대분류"] = _apply_cost_bucket(cost_df)
    cost_df = cost_df[cost_df["대분류"].notna()]
    result = (
        cost_df.groupby(["대분류", "계정과목"])["차변"]
        .sum()
        .reset_index()
        .rename(columns={"차변": "금액"})
        .sort_values(["대분류", "금액"], ascending=[True, False])
    )
    return result


def calc_health_score(current: dict, prev: Optional[dict] = None) -> dict:
    """건강점수 100점 + 4대 리스크 (CLAUDE.md §9).

    prev가 None이면 기준월 — 점수 계산 없이 baseline 표시.
    """
    if prev is None:
        return {
            "총점": None,
            "리스크": {
                "마진잠식": {"점수": None, "상태": "baseline", "사유": "기준월 (비교 데이터 없음)"},
                "대손결운": {"점수": None, "상태": "baseline", "사유": "기준월 (비교 데이터 없음)"},
                "자금경색": {"점수": None, "상태": "baseline", "사유": "기준월 (비교 데이터 없음)"},
                "집중리스크": {"점수": None, "상태": "baseline", "사유": "기준월 (비교 데이터 없음)"},
            },
            "is_baseline": True,
        }

    scores = {}
    reasons = {}

    # 1. 마진잠식 (35점)
    margin_diff = current["영업이익률"] - prev["영업이익률"]
    labor_diff = current["인건비율"] - prev["인건비율"]
    margin_score = 35
    margin_reason = "정상"
    if margin_diff < -5:
        margin_score -= 20
        margin_reason = f"영업이익률 전월비 {margin_diff:+.1f}%p 하락"
    elif margin_diff < -2:
        margin_score -= 10
        margin_reason = f"영업이익률 전월비 {margin_diff:+.1f}%p 하락"
    elif margin_diff < 0:
        margin_score -= 5
        margin_reason = f"영업이익률 전월비 {margin_diff:+.1f}%p 소폭 하락"
    if labor_diff > 3:
        margin_score -= 10
        margin_reason += f" / 인건비율 {labor_diff:+.1f}%p 상승"
    elif labor_diff > 1:
        margin_score -= 5
        margin_reason += f" / 인건비율 {labor_diff:+.1f}%p 상승"
    scores["마진잠식"] = max(0, margin_score)
    reasons["마진잠식"] = margin_reason if margin_reason != "정상" else "영업이익률·인건비율 전월 수준 유지"

    # 2. 대손결운 (25점)
    ar_diff = current.get("악성미수비중", 0) - prev.get("악성미수비중", 0)
    ar_score = 25
    ar_reason = "정상"
    if ar_diff > 10:
        ar_score -= 15
        ar_reason = f"악성미수(91일+) 비중 {ar_diff:+.1f}%p 증가"
    elif ar_diff > 5:
        ar_score -= 8
        ar_reason = f"악성미수(91일+) 비중 {ar_diff:+.1f}%p 증가"
    elif ar_diff > 0:
        ar_score -= 3
        ar_reason = "악성미수(91일+) 비중 소폭 증가"
    scores["대손결운"] = max(0, ar_score)
    reasons["대손결운"] = ar_reason if ar_reason != "정상" else "악성미수 비중 전월 수준"

    # 3. 자금경색 (20점)
    scores["자금경색"] = 20
    reasons["자금경색"] = "현금 데이터 수동 입력 필요"

    # 4. 집중리스크 (20점)
    conc = current.get("상위5집중도", 51.5)
    conc_score = 20
    conc_reason = "정상"
    if conc >= 60:
        conc_score -= 15
        conc_reason = f"상위 5개사 미수 집중도 {conc:.1f}% (매우 위험)"
    elif conc >= 50:
        conc_score -= 8
        conc_reason = f"상위 5개사 미수 집중도 {conc:.1f}% (주의)"
    elif conc >= 40:
        conc_score -= 3
        conc_reason = f"상위 5개사 미수 집중도 {conc:.1f}%"
    scores["집중리스크"] = max(0, conc_score)
    reasons["집중리스크"] = conc_reason if conc_reason != "정상" else f"집중도 {conc:.1f}% 양호"

    총점 = sum(scores.values())

    def status(s, max_s):
        ratio = s / max_s
        return "green" if ratio >= 0.8 else ("yellow" if ratio >= 0.5 else "red")

    리스크 = {
        "마진잠식": {"점수": scores["마진잠식"], "만점": 35, "상태": status(scores["마진잠식"], 35), "사유": reasons["마진잠식"]},
        "대손결운": {"점수": scores["대손결운"], "만점": 25, "상태": status(scores["대손결운"], 25), "사유": reasons["대손결운"]},
        "자금경색": {"점수": scores["자금경색"], "만점": 20, "상태": status(scores["자금경색"], 20), "사유": reasons["자금경색"]},
        "집중리스크": {"점수": scores["집중리스크"], "만점": 20, "상태": status(scores["집중리스크"], 20), "사유": reasons["집중리스크"]},
    }

    return {"총점": 총점, "리스크": 리스크, "is_baseline": False}
