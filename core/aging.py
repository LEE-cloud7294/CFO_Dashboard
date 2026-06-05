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


def _merge_corrections(debits_df: pd.DataFrame, grp: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """차변 음수 정정전표를 대변으로 흡수.

    차변 < 0인 행 = 발생 취소 정정전표 → abs(차변)을 대변으로 변환해 credits에 합산.
    반환: (credits_with_corrections, has_correction_flag)
    """
    neg_debits = grp[grp["차변"] < 0].copy()
    has_correction = not neg_debits.empty

    credits = grp[grp["대변"] > 0][["전표일자", "대변"]].sort_values("전표일자")

    if has_correction:
        extra = neg_debits.assign(대변=neg_debits["차변"].abs())[["전표일자", "대변"]]
        credits = pd.concat([credits, extra]).sort_values("전표일자").reset_index(drop=True)

    return credits, has_correction


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

    - 차변 음수(정정전표) → 대변으로 흡수 후 FIFO 차감
    - 반환 컬럼: 거래처, 잔액, 정상(0-30), 주의(31-60), 경고(61-90), 악성(91+),
                 마지막입금일, 경과일, 정정전표
    """
    COLS = ["거래처", "잔액", "정상(0-30)", "주의(31-60)", "경고(61-90)", "악성(91+)",
            "마지막입금일", "경과일", "정정전표"]

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
        credits, has_correction = _merge_corrections(debits, grp)

        # 마지막 입금일 (정정전표 포함 모든 차감 이벤트 최근일)
        last_pmt = credits["전표일자"].max() if not credits.empty else None
        days_since = int((as_of_ts - last_pmt).days) if last_pmt is not None else None

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
            "마지막입금일": last_pmt.date() if last_pmt is not None else None,
            "경과일": days_since,
            "정정전표": has_correction,
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

    DSO = FIFO 매칭 후 (회수일 - 발생일) 가중평균.
    반환 컬럼: 거래처, 발생액, 회수액, 잔액, 회수율(%), DSO(일), 마지막입금일, 경과일, 정정전표
    """
    df = _fill_ar_partner(df)
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    ar_df["전표일자"] = pd.to_datetime(ar_df["전표일자"], errors="coerce")
    ar_df = ar_df.dropna(subset=["전표일자"])

    if ar_df.empty:
        return pd.DataFrame(columns=["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)", "마지막입금일", "경과일", "정정전표"])

    today_ts = pd.Timestamp(date.today())
    result = []

    for partner, grp in ar_df.groupby("거래처"):
        debits = grp[grp["차변"] > 0][["전표일자", "차변"]].sort_values("전표일자")
        credits, has_correction = _merge_corrections(debits, grp)

        발생액 = debits["차변"].sum()
        회수액 = credits["대변"].sum()
        잔액 = max(발생액 - 회수액, 0)
        회수율 = (min(회수액, 발생액) / 발생액 * 100) if 발생액 > 0 else 0

        last_pmt = credits["전표일자"].max() if not credits.empty else None
        days_since = int((today_ts - last_pmt).days) if last_pmt is not None else None

        # FIFO 매칭으로 DSO 계산
        open_items = [[row["전표일자"], row["차변"]] for _, row in debits.iterrows()]
        dso_pairs = []

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
            "마지막입금일": last_pmt.date() if last_pmt is not None else None,
            "경과일": days_since,
            "정정전표": has_correction,
        })

    if not result:
        return pd.DataFrame(columns=["거래처", "발생액", "회수액", "잔액", "회수율(%)", "DSO(일)", "마지막입금일", "경과일", "정정전표"])

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


def calc_payment_pattern(df: pd.DataFrame) -> pd.DataFrame:
    """거래처별 실제 결제 기간 분포 분석 — 여신 관리 핵심 지표.

    FIFO 매칭으로 각 발생 건이 실제로 얼마 만에 회수됐는지 추적.
    차변 음수(정정전표) → 대변으로 흡수 후 처리.
    """
    df = _fill_ar_partner(df)
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    ar_df["전표일자"] = pd.to_datetime(ar_df["전표일자"], errors="coerce")
    ar_df = ar_df.dropna(subset=["전표일자"])

    if ar_df.empty:
        return pd.DataFrame()

    result = []

    for partner, grp in ar_df.groupby("거래처"):
        debits = grp[grp["차변"] > 0][["전표일자", "차변"]].sort_values("전표일자")
        credits, _ = _merge_corrections(debits, grp)

        if debits.empty:
            continue

        발생액 = debits["차변"].sum()
        회수액 = credits["대변"].sum()

        # FIFO 매칭 → 각 발생 건의 회수 소요일
        open_items = [[row["전표일자"], row["차변"]] for _, row in debits.iterrows()]
        matched = []

        for _, crow in credits.iterrows():
            remaining = crow["대변"]
            for item in open_items:
                if remaining <= 0:
                    break
                applied = min(item[1], remaining)
                if applied > 0:
                    days = (crow["전표일자"] - item[0]).days
                    if days >= 0:
                        matched.append((days, applied))
                    item[1] -= applied
                    remaining -= applied

        if not matched:
            continue

        total_matched = sum(a for _, a in matched)
        all_days = [d for d, _ in matched]

        avg_days = sum(d * a for d, a in matched) / total_matched

        sorted_matched = sorted(matched, key=lambda x: x[0])
        cumsum = 0
        median_days = sorted_matched[-1][0]
        for d, a in sorted_matched:
            cumsum += a
            if cumsum >= total_matched * 0.5:
                median_days = d
                break

        b30  = sum(a for d, a in matched if d <= 30)  / total_matched * 100
        b60  = sum(a for d, a in matched if 31 <= d <= 60)  / total_matched * 100
        b90  = sum(a for d, a in matched if 61 <= d <= 90)  / total_matched * 100
        b91p = sum(a for d, a in matched if d > 90)  / total_matched * 100

        sorted_days = sorted(matched, key=lambda x: x[0])
        cum = 0
        p95_days = sorted_days[-1][0]
        for d, a in sorted_days:
            cum += a
            if cum >= total_matched * 0.95:
                p95_days = d
                break
        recommended = ((p95_days // 30) + (1 if p95_days % 30 > 0 else 0)) * 30

        result.append({
            "거래처":        partner,
            "발생액":        round(발생액),
            "회수액":        round(회수액),
            "잔액":          round(max(발생액 - 회수액, 0)),
            "회수율(%)":     round(min(회수액, 발생액) / 발생액 * 100, 1) if 발생액 > 0 else 0,
            "평균결제일":    round(avg_days),
            "중간결제일":    median_days,
            "최소결제일":    min(all_days),
            "최대결제일":    max(all_days),
            "30일이내(%)":   round(b30, 1),
            "31-60일(%)":    round(b60, 1),
            "61-90일(%)":    round(b90, 1),
            "91일이상(%)":   round(b91p, 1),
            "권장여신(일)":  recommended,
        })

    if not result:
        return pd.DataFrame()

    return (
        pd.DataFrame(result)
        .sort_values("발생액", ascending=False)
        .reset_index(drop=True)
    )


def calc_bills_receivable(df: pd.DataFrame) -> dict:
    """110(받을어음) 잔액 분석 — 어음수취 후 미현금화 리스크."""
    ar_df = df[df["계정코드"].astype(str).str.startswith("108")].copy()
    bill_df = df[df["계정코드"].astype(str).str.startswith("110")].copy()

    total_110 = bill_df["차변"].sum() - bill_df["대변"].sum()

    ar_df = _fill_ar_partner(ar_df)
    partner_bills = {}

    for vno, grp in df.groupby("전표번호"):
        ar_creds = grp[grp["계정코드"].astype(str).str.startswith("108") & (grp["대변"] > 0)]
        bill_in  = grp[grp["계정코드"].astype(str).str.startswith("110") & (grp["차변"] > 0)]

        if ar_creds.empty or bill_in.empty:
            continue

        for _, row in ar_creds.iterrows():
            partner = str(row.get("거래처", "")).strip()
            amt = row["대변"]
            if partner:
                partner_bills[partner] = partner_bills.get(partner, 0) + amt

    by_partner = (
        pd.DataFrame([
            {"거래처": p, "어음수취액": round(v)}
            for p, v in sorted(partner_bills.items(), key=lambda x: -x[1])
        ])
        if partner_bills else pd.DataFrame(columns=["거래처", "어음수취액"])
    )

    return {
        "미현금화잔액": round(total_110),
        "거래처별": by_partner,
    }
