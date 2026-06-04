import pandas as pd
from datetime import date


def _fill_ar_partner(df: pd.DataFrame) -> pd.DataFrame:
    """108 대변(회수) 행의 빈 거래처를 같은 전표의 108 차변 행에서 보완.

    위하고 수금 처리 시 108 대변 행의 거래처가 공란인 경우가 많음.
    같은 전표번호의 108 차변(발생) 행 → 임의 행 순으로 보완.
    """
    if "전표번호" not in df.columns:
        return df

    df = df.copy()
    ar = df[df["계정코드"].astype(str).str.startswith("108")]

    # 우선: 같은 전표의 108 차변 행 거래처
    debit_map = (
        ar[(ar["차변"] > 0) & (ar["거래처"].astype(str).str.strip() != "")]
        .groupby("전표번호")["거래처"]
        .first()
        .to_dict()
    )
    # 차선: 같은 전표의 임의 행 거래처
    valid = df[df["거래처"].astype(str).str.strip() != ""]
    fallback_map = (
        valid.groupby("전표번호")["거래처"].first().to_dict()
        if not valid.empty else {}
    )

    mask = (
        df["계정코드"].astype(str).str.startswith("108")
        & (df["대변"] > 0)
        & (df["거래처"].astype(str).str.strip() == "")
    )
    if mask.any():
        def get_partner(vno):
            return debit_map.get(vno) or fallback_map.get(vno, "")
        df.loc[mask, "거래처"] = df.loc[mask, "전표번호"].map(get_partner).fillna("")

    return df


def extract_ar_collections(df: pd.DataFrame) -> pd.DataFrame:
    """108 대변(채권 감소) 이벤트 추출 — 회수 수단 판별 포함.

    반환 컬럼: 전표번호, 일자, 거래처, 회수액, 수단(현금/어음수취/잡손실절사)
    """
    df = _fill_ar_partner(df)
    collections = []

    for vno, grp in df.groupby("전표번호"):
        ar_credits = grp[
            (grp["계정코드"].astype(str).str.startswith("108"))
            & (grp["대변"] > 0)
        ]
        if ar_credits.empty:
            continue

        bill_in = grp[
            (grp["계정코드"].astype(str).str.startswith("110"))
            & (grp["차변"] > 0)
        ]

        for _, ar in ar_credits.iterrows():
            거래처 = str(ar["거래처"]).strip()
            금액 = ar["대변"]
            적요 = str(ar.get("적요", ""))

            # 회수 수단 판별
            if "잡손실" in 적요:
                수단 = "잡손실절사"
            elif "받을어음" in 적요 or "어음" in 적요 or "보관" in 적요:
                수단 = "어음수취"
            elif not bill_in.empty and 거래처 in bill_in["거래처"].values:
                수단 = "어음수취"
            else:
                수단 = "현금"

            collections.append({
                "전표번호": vno,
                "일자": ar["전표일자"],
                "거래처": 거래처,
                "회수액": 금액,
                "수단": 수단,
            })

    if not collections:
        return pd.DataFrame(columns=["전표번호", "일자", "거래처", "회수액", "수단"])
    return pd.DataFrame(collections)


def calc_aging(df: pd.DataFrame, as_of: date = None) -> pd.DataFrame:
    """108(외상매출금) 거래처별 FIFO 연령분석.

    발생(108 차변)과 회수(108 대변)를 분리해 오래된 발생부터 회수를 차감.
    잔여 발생 건의 날짜로 aging 구간 결정.

    반환 컬럼: 거래처, 잔액, 정상(0-30), 주의(31-60), 경고(61-90), 악성(91+)
    """
    COLS = ["거래처", "잔액", "정상(0-30)", "주의(31-60)", "경고(61-90)", "악성(91+)"]

    if as_of is None:
        dates = pd.to_datetime(df["전표일자"], errors="coerce").dropna()
        as_of = dates.max().date() if not dates.empty else date.today()

    df = _fill_ar_partner(df)
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    ar_df["전표일자"] = pd.to_datetime(ar_df["전표일자"], errors="coerce")
    ar_df = ar_df.dropna(subset=["전표일자"])

    if ar_df.empty:
        return pd.DataFrame(columns=COLS)

    as_of_ts = pd.Timestamp(as_of)
    result = []

    for partner, grp in ar_df.groupby("거래처"):
        debits = grp[grp["차변"] > 0][["전표일자", "차변"]].sort_values("전표일자")
        credits = grp[grp["대변"] > 0][["전표일자", "대변"]].sort_values("전표일자")

        # FIFO 큐: [발생일, 잔여금액]
        open_items = [[row["전표일자"], row["차변"]] for _, row in debits.iterrows()]

        # 오래된 발생부터 회수 차감
        for _, crow in credits.iterrows():
            remaining = crow["대변"]
            for item in open_items:
                if remaining <= 0:
                    break
                applied = min(item[1], remaining)
                item[1] -= applied
                remaining -= applied

        # 잔여 발생 (1원 미만 절사)
        outstanding = [(d, a) for d, a in open_items if a > 1]
        if not outstanding:
            continue

        buckets = {"정상(0-30)": 0, "주의(31-60)": 0, "경고(61-90)": 0, "악성(91+)": 0}
        for d, a in outstanding:
            days = (as_of_ts - d).days
            if days <= 30:
                buckets["정상(0-30)"] += a
            elif days <= 60:
                buckets["주의(31-60)"] += a
            elif days <= 90:
                buckets["경고(61-90)"] += a
            else:
                buckets["악성(91+)"] += a

        total = sum(a for _, a in outstanding)
        result.append({
            "거래처": partner,
            "잔액": round(total),
            "정상(0-30)": round(buckets["정상(0-30)"]),
            "주의(31-60)": round(buckets["주의(31-60)"]),
            "경고(61-90)": round(buckets["경고(61-90)"]),
            "악성(91+)": round(buckets["악성(91+)"]),
        })

    if not result:
        return pd.DataFrame(columns=COLS)

    return (
        pd.DataFrame(result)
        .sort_values("잔액", ascending=False)
        .reset_index(drop=True)
    )


def calc_ar_summary(df: pd.DataFrame) -> pd.DataFrame:
    """거래처별 AR 발생액 / 회수액 / 회수율 / DSO.

    DSO = FIFO 매칭 후 (회수일 - 발생일) 가중평균. 단일 월은 의미 없고 누적 기준으로 사용.
    반환 컬럼: 거래처, 발생액, 회수액, 잔액, 회수율(%), DSO(일)
    """
    df = _fill_ar_partner(df)
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    ar_df["전표일자"] = pd.to_datetime(ar_df["전표일자"], errors="coerce")
    ar_df = ar_df.dropna(subset=["전표일자"])

    if ar_df.empty:
        return pd.DataFrame(columns=["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)"])

    result = []

    for partner, grp in ar_df.groupby("거래처"):
        debits = grp[grp["차변"] > 0][["전표일자", "차변"]].sort_values("전표일자")
        credits = grp[grp["대변"] > 0][["전표일자", "대변"]].sort_values("전표일자")

        발생액 = debits["차변"].sum()
        회수액 = credits["대변"].sum()
        잔액 = max(발생액 - 회수액, 0)
        회수율 = (min(회수액, 발생액) / 발생액 * 100) if 발생액 > 0 else 0

        # FIFO 매칭으로 DSO 계산
        open_items = [[row["전표일자"], row["차변"]] for _, row in debits.iterrows()]
        dso_pairs = []  # (days, amount)

        for _, crow in credits.iterrows():
            remaining = crow["대변"]
            for item in open_items:
                if remaining <= 0:
                    break
                applied = min(item[1], remaining)
                if applied > 0:
                    days = (crow["전표일자"] - item[0]).days
                    if days >= 0:
                        dso_pairs.append((days, applied))
                    item[1] -= applied
                    remaining -= applied

        dso = (
            sum(d * a for d, a in dso_pairs) / sum(a for _, a in dso_pairs)
            if dso_pairs else None
        )

        result.append({
            "거래처": partner,
            "발생액": round(발생액),
            "회수액": round(회수액),
            "잔액": round(잔액),
            "회수율(%)": round(회수율, 1),
            "DSO(일)": round(dso) if dso is not None else None,
        })

    if not result:
        return pd.DataFrame(columns=["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)"])

    return (
        pd.DataFrame(result)
        .sort_values("발생액", ascending=False)
        .reset_index(drop=True)
    )


def calc_concentration(aging_df: pd.DataFrame) -> dict:
    """거래처 집중도 분석 (상위 5개사 비중)."""
    if aging_df.empty:
        return {"상위5집중도": 0, "상위5": [], "총잔액": 0, "총거래처수": 0}

    총잔액 = aging_df["잔액"].sum()
    top5 = aging_df.head(5)
    집중도 = (top5["잔액"].sum() / 총잔액 * 100) if 총잔액 > 0 else 0

    return {
        "상위5집중도": round(집중도, 1),
        "상위5": top5[["거래처", "잔액"]].to_dict("records"),
        "총잔액": round(총잔액),
        "총거래처수": len(aging_df),
    }


def calc_overdue_ratio(aging_df: pd.DataFrame) -> dict:
    """연체 비중 요약."""
    if aging_df.empty:
        return {"정상비중": 0, "주의비중": 0, "경고비중": 0, "악성비중": 0}
    total = aging_df["잔액"].sum()
    if total == 0:
        return {"정상비중": 0, "주의비중": 0, "경고비중": 0, "악성비중": 0}
    return {
        "정상비중": round(aging_df["정상(0-30)"].sum() / total * 100, 1),
        "주의비중": round(aging_df["주의(31-60)"].sum() / total * 100, 1),
        "경고비중": round(aging_df["경고(61-90)"].sum() / total * 100, 1),
        "악성비중": round(aging_df["악성(91+)"].sum() / total * 100, 1),
    }
