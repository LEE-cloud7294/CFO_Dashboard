import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import load_journal, get_available_months, get_annual_dep
from core.metrics import calc_kpi, apply_monthly_depreciation, fmt_krw

st.set_page_config(page_title="손익계산서", page_icon="📄", layout="wide")

# 인쇄용 CSS (사이드바·헤더 숨김)
st.markdown("""
<style>
@media print {
    [data-testid="stSidebar"], [data-testid="stHeader"],
    [data-testid="stToolbar"], .stButton, .stSelectbox,
    .stRadio, [data-testid="stDecoration"] {
        display: none !important;
    }
    .block-container { padding: 0 !important; }
    body { background: white !important; color: black !important; }
    .pl-table { color: black !important; }
}
.pl-table {
    font-family: 'Pretendard', 'Malgun Gothic', sans-serif;
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}
.pl-table td { padding: 6px 12px; border-bottom: 1px solid #374151; }
.pl-table .header-row td {
    background: #1e3a5f; color: white; font-weight: bold;
    border-bottom: 2px solid #60a5fa;
}
.pl-table .subtotal-row td { font-weight: bold; border-top: 2px solid #60a5fa; }
.pl-table .total-row td {
    font-weight: bold; font-size: 16px;
    border-top: 2px solid #60a5fa; border-bottom: 2px solid #60a5fa;
}
.pl-table .profit-pos { color: #4ade80; }
.pl-table .profit-neg { color: #f87171; }
.pl-table td.right { text-align: right; }
.pl-table td.center { text-align: center; }
</style>
""", unsafe_allow_html=True)

if not st.session_state.get("authenticated"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("확인"):
        if pw == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

# ── 선택 ──────────────────────────────────────────────────────────────────
months = get_available_months()
if not months:
    st.warning("데이터가 없습니다.")
    st.stop()

col_mode, col_sel, col_print = st.columns([1, 2, 1])

mode = col_mode.radio("기간", ["월별", "연간"], horizontal=True)
all_years = sorted(set(m[:4] for m in months))

if mode == "월별":
    selected_ym = col_sel.selectbox("기준 월", months, index=0)
    selected_year = selected_ym[:4]
else:
    selected_year = col_sel.selectbox("기준 연도", all_years[::-1], index=0)
    selected_ym = None

col_print.markdown("<br>", unsafe_allow_html=True)
if col_print.button("🖨️ 인쇄 / PDF 저장", use_container_width=True):
    st.markdown("<script>window.print();</script>", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_kpi_month(ym: str) -> dict:
    df = load_journal(ym)
    if df.empty:
        return {}
    if "계정그룹" not in df.columns:
        df["계정그룹"] = df["계정코드"].astype(str).str[:1]
    return calc_kpi(df)


@st.cache_data(ttl=600)
def load_kpi_year(year: str, month_list: list[str]) -> dict:
    year_months = [m for m in month_list if m.startswith(year)]
    all_dfs = []
    for ym in year_months:
        df = load_journal(ym)
        if not df.empty:
            if "계정그룹" not in df.columns:
                df["계정그룹"] = df["계정코드"].astype(str).str[:1]
            all_dfs.append(df)
    if not all_dfs:
        return {}
    combined = pd.concat(all_dfs, ignore_index=True)
    return calc_kpi(combined)


# ── 현재 / 비교 KPI 로드 ───────────────────────────────────────────────────
if mode == "월별":
    cur_kpi = load_kpi_month(selected_ym)
    # 전월
    idx = months.index(selected_ym)
    prev_kpi = load_kpi_month(months[idx + 1]) if idx < len(months) - 1 else {}
    title_cur = f"{selected_ym[:4]}년 {selected_ym[5:7]}월"
    title_prev = f"전월 대비"
else:
    cur_kpi = load_kpi_year(selected_year, months)
    # 전년
    prev_years = [y for y in all_years if y < selected_year]
    prev_kpi = load_kpi_year(prev_years[-1], months) if prev_years else {}
    title_cur = f"{selected_year}년 연간"
    title_prev = "전년 대비"

if not cur_kpi:
    st.error("선택한 기간의 데이터가 없습니다.")
    st.stop()

# ── 감가상각 ÷12 자동 적용 ──────────────────────────────────────────────────
# 우선순위: tax_journal(세무사) > 직원분개장 12월 자동추출
# 토글 없이 항상 균등 배분 → 매달 동일한 감가상각비 표시
@st.cache_data(ttl=3600)
def cached_annual_dep(year: str) -> float:
    return get_annual_dep(year)

annual_dep = cached_annual_dep(selected_year)
monthly_dep = annual_dep / 12
dep_display = monthly_dep if mode == "월별" else annual_dep
use_dep_line = dep_display > 0

if use_dep_line:
    cur_kpi = apply_monthly_depreciation(cur_kpi, dep_display)
    dep_source = "세무사 분개장" if True else "직원 분개장 12월"
    st.caption(
        f"📊 감가상각 자동 배분 — {selected_year}년 연간 {annual_dep/1e8:.3f}억 ÷ 12 = "
        f"{'월 ' + str(round(monthly_dep/1e6, 1)) + '백만원' if mode == '월별' else str(round(annual_dep/1e8, 3)) + '억원/년'}"
    )


def fmt_억(v: float, sign: bool = False) -> str:
    s = "+" if sign and v > 0 else ""
    return s + fmt_krw(v)


def pct(v: float, base: float) -> str:
    if base <= 0:
        return "—"
    return f"{v / base * 100:.1f}%"


def diff_pct(cur: float, prv: float) -> str:
    if prv == 0:
        return "—"
    d = (cur - prv) / abs(prv) * 100
    return f"{d:+.1f}%"


def margin_diff(cur_kpi: dict, prev_kpi: dict, key: str = "영업이익률_v7") -> str:
    if not prev_kpi:
        return "—"
    d = cur_kpi.get(key, 0) - prev_kpi.get(key, 0)
    return f"{d:+.1f}%p"


# ── 손익 계산서 테이블 ────────────────────────────────────────────────────
st.markdown("---")

매출 = cur_kpi.get("매출액", 0)
원재료순 = cur_kpi.get("원재료순", 0)
부재료매입 = cur_kpi.get("부재료매입", 0)
인건비 = cur_kpi.get("인건비", 0)
영업이익 = cur_kpi.get("영업이익_v7", 0)
이자비용 = cur_kpi.get("이자비용", 0)
자산처분손실 = cur_kpi.get("자산처분손실", 0)
영업외수익_반복 = cur_kpi.get("영업외수익_반복", 0)
영업외수익_일회성 = cur_kpi.get("영업외수익_일회성", 0)
실질이익 = cur_kpi.get("실질이익", 0)
buckets = cur_kpi.get("비용대분류_v7", {})

전력수도 = buckets.get("전력·수도", 0)
물류차량 = buckets.get("물류·차량", 0)
유지소모 = buckets.get("유지·소모품", 0)
수수료 = buckets.get("수수료", 0)
세금임차 = buckets.get("세금·임차", 0)
보험료 = buckets.get("보험료", 0)
안전관리 = buckets.get("안전관리비", 0)
하드웨어 = buckets.get("하드웨어", 0)   # 소모공구비(소모공구·부품 매입)
대손상각 = buckets.get("대손상각", 0)
판관비 = buckets.get("판관비경비", 0)
연구개발 = buckets.get("연구개발", 0)
기타bucket = buckets.get("기타", 0)
기타비 = 기타bucket + 안전관리 + 하드웨어 + 대손상각 + 판관비 + 연구개발

prev_매출 = prev_kpi.get("매출액", 0) if prev_kpi else 0

profit_cls = "profit-pos" if 영업이익 >= 0 else "profit-neg"
real_cls = "profit-pos" if 실질이익 >= 0 else "profit-neg"

html = f"""
<div style='max-width:720px; margin:auto;'>
<h3 style='text-align:center; color:white;'>곡성안전유리 손익계산서</h3>
<p style='text-align:center; color:#9ca3af; font-size:13px;'>{title_cur}
{"  |  " + title_prev + " 매출 " + diff_pct(매출, prev_매출) + " / 이익률 " + margin_diff(cur_kpi, prev_kpi) if prev_kpi else ""}</p>
<table class='pl-table'>
  <colgroup>
    <col style='width:45%;'><col style='width:25%;'><col style='width:15%;'><col style='width:15%;'>
  </colgroup>
  <tr class='header-row'>
    <td>항목</td>
    <td class='right'>금액</td>
    <td class='right'>매출대비</td>
    <td class='right'>{title_prev}</td>
  </tr>
  <tr>
    <td>매출액</td>
    <td class='right'>{fmt_억(매출)}</td>
    <td class='right'>100%</td>
    <td class='right'>{diff_pct(매출, prev_매출) if prev_kpi else "—"}</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;원재료매입 <span style='font-size:11px;color:#6b7280;'>(*이달 매입)</span></td>
    <td class='right'>−{fmt_억(원재료순)}</td>
    <td class='right'>{pct(원재료순, 매출)}</td>
    <td class='right'>{diff_pct(원재료순, prev_kpi.get("원재료순",0)) if prev_kpi else "—"}</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;부재료매입</td>
    <td class='right'>−{fmt_억(부재료매입)}</td>
    <td class='right'>{pct(부재료매입, 매출)}</td>
    <td class='right'>{diff_pct(부재료매입, prev_kpi.get("부재료매입",0)) if prev_kpi else "—"}</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;인건비</td>
    <td class='right'>−{fmt_억(인건비)}</td>
    <td class='right'>{pct(인건비, 매출)}</td>
    <td class='right'>{diff_pct(인건비, prev_kpi.get("인건비",0)) if prev_kpi else "—"}</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;전력·수도</td>
    <td class='right'>−{fmt_억(전력수도)}</td>
    <td class='right'>{pct(전력수도, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;물류·차량</td>
    <td class='right'>−{fmt_억(물류차량)}</td>
    <td class='right'>{pct(물류차량, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;유지·소모품</td>
    <td class='right'>−{fmt_억(유지소모)}</td>
    <td class='right'>{pct(유지소모, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;수수료</td>
    <td class='right'>−{fmt_억(수수료)}</td>
    <td class='right'>{pct(수수료, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;세금·임차</td>
    <td class='right'>−{fmt_억(세금임차)}</td>
    <td class='right'>{pct(세금임차, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;보험료</td>
    <td class='right'>−{fmt_억(보험료)}</td>
    <td class='right'>{pct(보험료, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  <tr>
    <td>&nbsp;&nbsp;기타비용</td>
    <td class='right'>−{fmt_억(기타비)}</td>
    <td class='right'>{pct(기타비, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  {"" if 하드웨어 == 0 else f"<tr><td>&nbsp;&nbsp;&nbsp;&nbsp;<span style='font-size:11px;color:#9ca3af;'>└ 소모공구비</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>−{fmt_억(하드웨어)}</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>{pct(하드웨어, 매출)}</span></td><td></td></tr>"}
  {"" if 안전관리 == 0 else f"<tr><td>&nbsp;&nbsp;&nbsp;&nbsp;<span style='font-size:11px;color:#9ca3af;'>└ 안전관리비</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>−{fmt_억(안전관리)}</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>{pct(안전관리, 매출)}</span></td><td></td></tr>"}
  {"" if 판관비 == 0 else f"<tr><td>&nbsp;&nbsp;&nbsp;&nbsp;<span style='font-size:11px;color:#9ca3af;'>└ 판관비경비</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>−{fmt_억(판관비)}</span></td><td class='right'><span style='font-size:11px;color:#9ca3af;'>{pct(판관비, 매출)}</span></td><td></td></tr>"}
  {"" if not use_dep_line else f"<tr><td>&nbsp;&nbsp;감가상각비 <span style='font-size:11px;color:#60a5fa;'>(÷12 균등배분)</span></td><td class='right'>−{fmt_억(dep_display)}</td><td class='right'>{pct(dep_display, 매출)}</td><td class='right'>—</td></tr>"}
  <tr class='subtotal-row'>
    <td class='{profit_cls}'>영업이익</td>
    <td class='right {profit_cls}'>{fmt_억(영업이익, sign=True)}</td>
    <td class='right {profit_cls}'>{pct(영업이익, 매출)}</td>
    <td class='right'>{margin_diff(cur_kpi, prev_kpi) if prev_kpi else "—"}</td>
  </tr>
  {"" if 영업외수익_반복 == 0 else f"<tr><td>&nbsp;&nbsp;이자수익</td><td class='right'>+{fmt_억(영업외수익_반복)}</td><td class='right'>{pct(영업외수익_반복, 매출)}</td><td class='right'>—</td></tr>"}
  {"" if 영업외수익_일회성 == 0 else f"<tr><td>&nbsp;&nbsp;영업외수익 <span style='font-size:11px;color:#6b7280;'>(일회성)</span></td><td class='right'>+{fmt_억(영업외수익_일회성)}</td><td class='right'>{pct(영업외수익_일회성, 매출)}</td><td class='right'>—</td></tr>"}
  <tr>
    <td>&nbsp;&nbsp;이자비용</td>
    <td class='right'>−{fmt_억(이자비용)}</td>
    <td class='right'>{pct(이자비용, 매출)}</td>
    <td class='right'>—</td>
  </tr>
  {"" if 자산처분손실 == 0 else f"<tr><td>&nbsp;&nbsp;감가상각(연말일괄) <span style='font-size:11px;color:#6b7280;'>(현금기준)</span></td><td class='right'>−{fmt_억(자산처분손실)}</td><td class='right'>{pct(자산처분손실, 매출)}</td><td class='right'>—</td></tr>"}
  <tr class='total-row'>
    <td class='{real_cls}'>실질이익</td>
    <td class='right {real_cls}'>{fmt_억(실질이익, sign=True)}</td>
    <td class='right {real_cls}'>{pct(실질이익, 매출)}</td>
    <td class='right'>—</td>
  </tr>
</table>
<p style='font-size:11px; color:#6b7280; margin-top:8px;'>
(*) 원재료매입은 이달(이 기간) 매입 기준으로 재고 반영 전. 연말 회계 대체(Code 455)는 제외.
</p>
</div>
"""

st.markdown(html, unsafe_allow_html=True)

# ── 경보 코멘트 ──────────────────────────────────────────────────────────
st.markdown("---")
if prev_kpi:
    comments = []
    매출변동 = (매출 - prev_매출) / abs(prev_매출) * 100 if prev_매출 else 0
    margin_cur = cur_kpi.get("영업이익률_v7", 0)
    margin_prv = prev_kpi.get("영업이익률_v7", 0)
    diff_m = margin_cur - margin_prv

    if 매출변동 < -10:
        comments.append(f"🚨 매출 {매출변동:+.1f}% — 수요 위축 또는 거래처 이탈 확인 필요")
    elif 매출변동 < -5:
        comments.append(f"⚠️ 매출 {매출변동:+.1f}% — 주요 거래처 현황 점검")

    if diff_m < -3:
        comments.append(f"⚠️ 영업이익률 {diff_m:+.1f}%p 하락 — 원가 상승 여부 확인")
    elif diff_m > 3:
        comments.append(f"✅ 영업이익률 {diff_m:+.1f}%p 개선")

    if 실질이익 < 0:
        comments.append(f"🚨 실질 적자 {abs(실질이익)/1e6:.0f}백만원 — 이자 부담 포함 적자")

    if not comments:
        comments.append("✅ 전기 대비 안정적인 수준 유지")

    for c in comments:
        st.markdown(c)
