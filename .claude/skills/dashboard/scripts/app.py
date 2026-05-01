"""
STEP 7 - Streamlit 대시보드 (LLM-Free)
섹터별 시총 Top10 추적 + 6개월 주목 섹터 + 재무비율 + 뉴스 아카이브
"""
import json
import os
from datetime import date, datetime
from pathlib import Path

import sys

# Streamlit Cloud: CWD = 레포 루트이므로 스크립트 디렉토리를 명시적으로 추가
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from generate_scorecard import generate_scorecard
from quant_screener import calc_factor_scores, enrich_price_changes
from trade_note_manager import (
    load_notes, save_notes, calc_pnl, check_stop_alerts,
)
import github_storage as _gh

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]


# ─── 비밀번호 인증 ────────────────────────────────────────────────────────────
def _check_password() -> bool:
    """Streamlit secrets에 password가 있으면 로그인 요구, 없으면 로컬 개발로 간주."""
    try:
        required_pwd = st.secrets["password"]
    except (KeyError, FileNotFoundError):
        return True  # 로컬 개발: secrets 없으면 인증 생략

    if st.session_state.get("_authenticated"):
        return True

    st.title("🔒 Korea Stock Agent")
    pwd = st.text_input("비밀번호를 입력하세요", type="password")
    if st.button("로그인", use_container_width=True):
        if pwd == required_pwd:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()
    return False
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
HISTORY_DIR = BASE_DIR / "data" / "portfolio_history"

DART_PERIOD_LABELS = {
    "11011": "사업보고서(연간)", "11012": "반기보고서",
    "11013": "1분기보고서",     "11014": "3분기보고서",
}

KRX_TO_NEWS: dict[str, str] = {
    "전기·전자":      "반도체",
    "IT 서비스":      "IT서비스",
    "제약":           "바이오",
    "의료·정밀기기":  "바이오",
    "전기·가스":      "에너지",
    "전기·가스·수도": "에너지",
    "운송장비·부품":  "자동차",
    "금속":           "철강",
    "화학":           "화학",
    "금융":           "금융",
    "은행":           "금융",
    "보험":           "금융",
    "증권":           "금융",
    "기타금융":       "금융",
    "건설":           "건설",
    "유통":           "유통",
    "통신":           "통신",
}

PERIOD_TO_COL = {"1d": "1일전", "7d": "7일전", "15d": "15일전", "30d": "30일전"}

RADAR_COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]

ACTION_LOG_PATH    = BASE_DIR / "data" / "action_log.json"
ALLOC_PATH         = BASE_DIR / "data" / "asset_allocation.json"
TRADE_NOTES_BASE   = BASE_DIR  # trade_note_manager가 BASE_DIR/data/trade_notes.json 사용
TRADE_NOTES_GH_PATH = "data/trade_notes.json"  # GitHub 레포 내 경로


def _load_notes_smart() -> dict:
    """GitHub 스토리지 우선, 없으면 로컬 파일. 세션 내 캐싱으로 API 호출 최소화."""
    if "_notes_cache" not in st.session_state:
        if _gh.is_available():
            data = _gh.load(TRADE_NOTES_GH_PATH)
            st.session_state["_notes_cache"] = data if data is not None else load_notes(TRADE_NOTES_BASE)
        else:
            st.session_state["_notes_cache"] = load_notes(TRADE_NOTES_BASE)
    return st.session_state["_notes_cache"]


def _save_notes_smart(notes: dict) -> None:
    """GitHub 저장 우선, 없으면 로컬 파일. 세션 캐시도 동시 갱신."""
    st.session_state["_notes_cache"] = notes
    if _gh.is_available():
        if not _gh.save(TRADE_NOTES_GH_PATH, notes):
            st.warning("⚠️ GitHub 저장 실패 — 로컬에 임시 저장됩니다.", icon="⚠️")
            save_notes(notes, TRADE_NOTES_BASE)
    else:
        save_notes(notes, TRADE_NOTES_BASE)


def load_alloc() -> dict:
    if ALLOC_PATH.exists():
        try:
            return json.loads(ALLOC_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"stock": 35, "cash": 50, "gold": 15}


def save_alloc(d: dict) -> None:
    ALLOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALLOC_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def load_action_log() -> dict:
    if ACTION_LOG_PATH.exists():
        try:
            return json.loads(ACTION_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_action_log(log: dict) -> None:
    ACTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTION_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


st.set_page_config(page_title="Korea Stock Agent", page_icon="📈", layout="wide")
_check_password()


# ─── 유틸 ────────────────────────────────────────────────────────────────────

def load_json_safe(filename: str, silent: bool = False) -> dict | list | None:
    path = OUTPUT_DIR / filename
    if not path.exists():
        if not silent:
            st.warning(f"데이터 없음: `{filename}`  ← 파이프라인 실행 필요")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        st.error(f"`{filename}` 파싱 오류: {e}")
        return None


def file_mtime(filename: str) -> str:
    path = OUTPUT_DIR / filename
    if path.exists():
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%m/%d %H:%M")
    return "없음"


def fmt(val, suffix="", decimals=2):
    if val is None:
        return "-"
    try:
        v = round(float(val), decimals)
        return f"{int(v):,}{suffix}" if v == int(v) else f"{v:,}{suffix}"
    except (TypeError, ValueError):
        return "-"


def decode_period(period_str: str | None) -> str:
    if not period_str:
        return "-"
    parts = period_str.split("_")
    if len(parts) == 2:
        return f"{parts[0]}년 {DART_PERIOD_LABELS.get(parts[1], parts[1])}"
    return period_str


def krx_display_name(krx_sector: str) -> str:
    news = KRX_TO_NEWS.get(krx_sector)
    if news and news != krx_sector:
        return f"{krx_sector} / {news}"
    return krx_sector


def sector_composite_score(krx_sector: str, sector_scores: dict, themes: dict) -> float:
    news_cat = KRX_TO_NEWS.get(krx_sector, krx_sector)
    sc = themes.get(news_cat, {}).get("composite_6m")
    if sc is not None:
        return sc
    return sector_scores.get(news_cat, {}).get("composite_score", 0.0) or 0.0


def is_highlighted(krx_sector: str, themes: dict) -> bool:
    news_cat = KRX_TO_NEWS.get(krx_sector, krx_sector)
    return themes.get(news_cat, {}).get("highlight", False)


def _trimmed_median(vals: list[float], cap: float | None = None) -> float | None:
    if not vals:
        return None
    filtered = [v for v in vals if cap is None or abs(v) <= cap]
    if not filtered:
        filtered = vals
    s = sorted(filtered)
    n = len(s)
    return round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 2)


def _trimmed_mean(vals: list[float], trim_pct: float = 0.10) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    cut = max(1, int(len(s) * trim_pct))
    trimmed = s[cut:-cut] if len(s) > cut * 2 else s
    return round(sum(trimmed) / len(trimmed), 2)


def calc_sector_averages(market_data: dict, ratios_data: dict) -> dict[str, dict]:
    fields = ["per", "roe", "pbr", "debt_ratio", "revenue_growth"]
    sector_vals: dict[str, dict[str, list]] = {}
    for ticker, mkt in market_data.items():
        sector = mkt.get("sector")
        r = ratios_data.get(ticker, {})
        if not sector or r.get("disclosure_warning"):
            continue
        sv = sector_vals.setdefault(sector, {f: [] for f in fields})
        for f in fields:
            v = r.get(f)
            if v is not None:
                sv[f].append(v)
    result = {}
    for sector, fdict in sector_vals.items():
        roe_clean = [v for v in fdict["roe"] if -300 <= v <= 300]
        dr_clean = [v for v in fdict["debt_ratio"] if 0 <= v <= 5000]
        result[sector] = {
            "per":            _trimmed_median(fdict["per"], cap=300),
            "pbr":            _trimmed_median(fdict["pbr"], cap=50),
            "roe":            _trimmed_mean(roe_clean) if roe_clean else None,
            "debt_ratio":     _trimmed_mean(dr_clean) if dr_clean else None,
            "revenue_growth": _trimmed_mean(fdict["revenue_growth"]),
        }
    return result


def delta_badge(val: float | None, avg: float | None, lower_better: bool = False) -> str:
    if val is None or avg is None or avg == 0:
        return fmt(val)
    pct = (val - avg) / abs(avg) * 100
    sign = "+" if pct >= 0 else ""
    return f"{fmt(val)} ({sign}{pct:.0f}%)"


def _norm_val(v, vmin, vmax, invert: bool = False) -> float:
    """0~1 정규화. invert=True 시 낮을수록 1에 가까움 (PER·PBR·부채비율)."""
    if v is None or vmin is None or vmax is None or vmax == vmin:
        return 0.5
    n = (v - vmin) / (vmax - vmin)
    n = max(0.0, min(1.0, n))
    return 1.0 - n if invert else n


# ─── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
section[data-testid="stSidebar"] > div:first-child { background-color: #f0f4f8; }
[data-testid="metric-container"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    transition: box-shadow 0.2s;
}
[data-testid="metric-container"]:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
h2 { border-left: 4px solid #1f77b4; padding-left: 10px; margin-top: 1.5rem; }
.stAlert p { font-size: 0.88rem; }
/* 모바일 대응 */
@media (max-width: 768px) {
    [data-testid="column"] { min-width: 48% !important; flex: 0 0 48% !important; }
    h1 { font-size: 1.4rem !important; }
    h2 { font-size: 1.1rem !important; }
    .stDataFrame { font-size: 0.72rem; }
    [data-testid="metric-container"] { padding: 8px 10px; }
}
/* 섹터 하이라이트 카드 */
.hl-card {
    background: linear-gradient(135deg,#fffbeb,#fef3c7);
    border: 2px solid #f59e0b;
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 6px;
}
/* 구분선 강화 */
hr { border-top: 2px solid #e2e8f0 !important; margin: 1.5rem 0 !important; }
</style>
""", unsafe_allow_html=True)


# ─── 데이터 로드 ──────────────────────────────────────────────────────────────

themes_data:       dict = load_json_safe("step2_themes.json") or {}
sector_scores:     dict = load_json_safe("step2_sector_scores.json", silent=True) or {}
rankings_data:     dict = load_json_safe("step2_sector_rankings.json") or {}
ratios_data:       dict = load_json_safe("step2_financial_ratios.json") or {}
news_preprocessed: dict = load_json_safe("step2_news_preprocessed.json", silent=True) or {}

market_data_raw: dict = {}
_mp = OUTPUT_DIR / "step1_market_data.json"
if _mp.exists():
    market_data_raw = json.loads(_mp.read_text(encoding="utf-8"))

sector_avgs = calc_sector_averages(market_data_raw, ratios_data) if market_data_raw and ratios_data else {}

# ─── 공통 파생값 ──────────────────────────────────────────────────────────────

all_new: list[tuple[str, str]] = []
all_removed: list[tuple[str, str]] = []
for _s, _v in rankings_data.items():
    _ch = _v.get("changes", {}).get("1d", {})
    _top10_map = {it["ticker"]: it.get("name", it["ticker"]) for it in _v.get("top10", [])}
    for _t in _ch.get("new_entries", []):
        _name = market_data_raw.get(_t, {}).get("name") or _top10_map.get(_t, _t)
        all_new.append((_name, _s))
    for _t in _ch.get("removed", []):
        _name = market_data_raw.get(_t, {}).get("name", _t)
        all_removed.append((_name, _s))

highlighted_count = sum(1 for t in themes_data.values() if t.get("highlight"))
per_ok = sum(1 for v in ratios_data.values() if v.get("per") is not None)

all_krx = sorted(rankings_data.keys())
hl_krx = [s for s in all_krx if is_highlighted(s, themes_data)]
non_hl_krx = [s for s in all_krx if not is_highlighted(s, themes_data)]
sector_options = (
    ["⭐ 주목 섹터 전체"]
    + [f"⭐ {krx_display_name(s)}" for s in hl_krx]
    + ["── 전체 ──"]
    + [krx_display_name(s) for s in non_hl_krx]
) if all_krx else ["데이터 없음"]

STEP_FILES = [
    ("STEP1-KRX",  "step1_market_data.json"),
    ("STEP1-DART", "step1_financial_data.json"),
    ("STEP1-뉴스", "step1_news_raw.json"),
    ("STEP2-순위", "step2_sector_rankings.json"),
    ("STEP2-테마", "step2_themes.json"),
    ("STEP2-비율", "step2_financial_ratios.json"),
]

# ─── 사이드바 ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📈 Korea Stock Agent")
    st.caption(f"기준일: {date.today().strftime('%Y-%m-%d')}")
    st.markdown("---")

    st.markdown("### ⚙️ 파이프라인 상태")
    for label, fname in STEP_FILES:
        ok = (OUTPUT_DIR / fname).exists()
        icon = "✅" if ok else "❌"
        mtime = file_mtime(fname) if ok else "없음"
        st.markdown(f"{icon} **{label}** `{mtime}`")

    st.markdown("---")
    st.markdown("### 📌 섹션 이동")
    st.markdown(
        '<a href="#action-cards">📋 오늘의 액션 카드</a><br>'
        '<a href="#highlight">⭐ 향후 6개월 주목 섹터</a><br>'
        '<a href="#top10">📊 섹터별 시총 Top10</a><br>'
        '<a href="#ratios">💹 재무비율 분석</a><br>'
        '<a href="#quant">🔬 퀀트 소형주 스크리너</a><br>'
        '<a href="#trade-note">📒 트레이드 노트</a><br>'
        '<a href="#ai-power">⚡ AI 전력 인프라</a><br>'
        '<a href="#disclosure">⚠️ 공시 경고</a><br>'
        '<a href="#pipeline">🔧 파이프라인 로그</a>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("### 🔍 종목 검색")
    st.text_input(
        "종목명 또는 티커",
        key="stock_search_query",
        placeholder="예: 삼성전자, 005930",
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### 🔧 표시 설정")
    compare_period = st.radio(
        "Top10 순위 비교 기간",
        ["1d", "7d", "15d", "30d"],
        horizontal=True,
        help="시총 Top10 테이블의 순위변동 비교 기준 기간",
    )
    selected_opt = st.selectbox(
        "재무비율 섹터 필터",
        sector_options,
        key="fin_sector_filter",
        help="재무비율 분석에 표시할 섹터를 선택하세요",
    )

    st.markdown("---")
    st.markdown("### 📄 스코어카드 PDF")

    @st.cache_data(show_spinner=False, ttl=3600)
    def _build_pdf(date_key: str) -> bytes:
        return generate_scorecard(rankings_data, ratios_data, themes_data)

    if rankings_data and ratios_data:
        pdf_bytes = _build_pdf(date.today().isoformat())
        st.download_button(
            label="⬇️ 오늘 스코어카드 다운로드",
            data=pdf_bytes,
            file_name=f"scorecard_{date.today().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            use_container_width=True,
            help="섹터별 시총 Top10 재무비율 PDF (주목 섹터 우선 정렬)",
        )
    else:
        st.caption("데이터 없음 — 파이프라인 실행 후 사용 가능")


# ─── 헤더 ────────────────────────────────────────────────────────────────────

st.title("📈 Korea Stock Agent — 섹터 리밸런싱 트래커")
st.caption(f"기준일: {date.today().strftime('%Y-%m-%d')} | 갱신: {datetime.now().strftime('%H:%M')}")

# ─── How to Use ───────────────────────────────────────────────────────────────

with st.expander("📖 이 대시보드 사용법 — 매매 워크플로우 전체 가이드", expanded=False):
    st.markdown("""
## 이 대시보드의 투자 철학

> **섹터 ETF 개념의 자기주도 리밸런싱.**
> 개별 종목 예측 대신, *섹터 전체의 방향성*과 *그 안에서 시장이 선택한 상위 종목*을 따라가는 전략입니다.
> 퀀트 데이터를 기반으로 매수·보유·매도 시점을 구조적으로 판단합니다.

---

## STEP 1 — 시장 진입 조건 확인 (상단 배너)

**계절성 시그널**부터 확인하세요.

| 시즌 | 기간 | 전략 |
|------|------|------|
| 🟢 공격 시즌 | 11월 ~ 4월 | 주식 비중 유지·확대, 적극 매수 검토 |
| 🟡 방어 시즌 | 5월 ~ 10월 | 비중 축소, 현금 확보, 신규 매수 자제 |

통계적으로 한국·미국 증시 모두 11~4월 수익률이 5~10월보다 유의미하게 높습니다.
방어 시즌에는 기존 보유 종목의 손절 기준을 더 엄격하게 적용하세요.

**자산배분 현황**에서 현재 포트폴리오 비중(주식/현금/금)을 권고치(35/50/15%)와 비교합니다.
주식 비중이 과다하면 일부 매도로 리밸런싱하고, 공격 시즌이면 주식 비중을 35% 이상으로 늘릴 수 있습니다.

---

## STEP 2 — 오늘의 액션 카드 확인

**가장 먼저 봐야 할 섹션**입니다. 어제 대비 섹터 시총 Top10에서:

- 🟢 **신규 편입 종목** → 매수 검토 대상. 해당 섹터가 주목 섹터이고 재무비율도 양호하면 매수 시그널.
- 🔴 **이탈 종목** → 보유 중이라면 매도 검토. Top10 복귀 여부를 7일~15일 관찰 후 판단.

액션 카드는 단독으로 매매 결정하지 말고, 아래 STEP 3~4와 교차 검증하세요.

---

## STEP 3 — 주목 섹터 선정 (6개월 트렌드)

**⭐ 향후 6개월 주목 섹터** 섹션에서 뉴스 감성 + 모멘텀 + 재무 종합점수가 높은 섹터를 확인합니다.

**섹터 선정 기준 (복수 선택 가능):**
1. 종합점수(composite_6m)가 상위인 섹터
2. 뉴스 감성·키워드 트렌드가 양호한 섹터
3. 계절성 공격 시즌과 겹치는 섹터

**실전 팁:** 한 번에 2~3개 섹터만 선택하세요. 섹터가 많아질수록 분산이 지나쳐 초과수익이 사라집니다.

---

## STEP 4 — 투자 종목 선별 (섹터 Top10 × 재무비율)

선정한 섹터의 **시총 Top10 종목**이 투자 유니버스입니다.
시총 상위 10종목은 해당 섹터를 시장이 이미 검증한 대표주입니다.

**재무비율 필터로 추가 검증:**

| 지표 | 좋은 기준 | 주의 기준 |
|------|-----------|-----------|
| PER | 섹터 평균 이하 | 섹터 평균 30% 초과 |
| ROE | 10% 이상 | 5% 미만 |
| 부채비율 | 100% 미만 | 200% 초과 |
| EPS 성장률 | 양수 | 3년 연속 음수 |
| 매출 성장률 | 양수 | 역성장 지속 |

**재무비율 분석** 섹션에서 각 지표를 섹터 평균과 비교한 배지(▲▼)로 확인하세요.
공시 경고(⚠️)가 있는 종목은 DART 공시 지연이므로 신중하게 판단하세요.

---

## STEP 5 — 매수 실행 및 트레이드 노트 기록

**분할 매수 원칙:**
- 선정 섹터 2~3개 × 섹터당 상위 5~7종목 = 총 10~20종목
- 종목당 균등 비중(예: 총 투자금의 5~10%)으로 분산
- 한 번에 전량 매수하지 말고 2~3회 분할 매수

**트레이드 노트**에 매수 즉시 기록하세요:
- 매수가, 수량, 매수일, 메모(매수 이유)
- 시스템이 자동으로 **손절가(매수가 ×0.9)** 와 **추적손절가(고점가 ×0.9)** 를 계산합니다.

---

## STEP 6 — 보유 중 모니터링 (리밸런싱 사이클)

**단기 리밸런싱 (1일~7일 주기):**
- 액션 카드의 이탈 종목 확인 → 보유 중이면 1주 관찰 후 복귀 없으면 매도
- 트레이드 노트에서 손절선·추적손절선 도달 여부 확인

**중기 리밸런싱 (15일~3개월 주기):**
- Top10 순위를 15d/30d 뷰로 확인 → 지속적으로 순위가 하락하는 종목은 교체
- 새로 Top10에 진입한 종목은 재무비율 검증 후 편입
- 이 사이클이 **ETF 리밸런싱과 동일한 원리**입니다

**3개월 이상 보유 판단 기준:**
- 섹터 자체가 6개월 주목 섹터에서 탈락 → 섹터 전체 비중 축소
- 계절성 방어 시즌 진입 → 비중 점진적 축소

---

## STEP 7 — 손절 규칙 (가장 중요)

감정이 아닌 숫자로 매도하세요.

| 시그널 | 조건 | 행동 |
|--------|------|------|
| ⚡ 손절 임박 | 매수가 대비 -5% | 주의 관찰, 추가 매수 금지 |
| 🚨 손절선 도달 | 매수가 대비 -10% | **즉시 매도** |
| ⚠️ 추적손절 도달 | 고점 대비 -10% | **즉시 매도** (수익 보호) |

Telegram 알림을 설정하면 손절 시그널을 실시간으로 받을 수 있습니다.
파이프라인이 매일 실행되며 고점가(peak_price)를 자동 갱신합니다.

---

## STEP 8 — 소형주 퀀트 보조 전략 (선택)

**퀀트 소형주 스크리너**는 메인 전략의 보조 수단입니다.
시총 하위 20% 소형주 중 저PER·저PBR·고성장 복합팩터 Top50을 선별합니다.

활용법:
- 메인 포트폴리오(섹터 Top10)의 위성 포지션으로 1~2종목 소액 편입
- 팩터점수 0.7 이상 + ROE 양수 + 재무비율 양호 조합이 최우선 후보
- 소형주 특성상 유동성 리스크가 있으므로 총 포트폴리오의 10~20% 이내로 제한

---

## 요약 — 한 눈에 보는 매매 루틴

```
매일 아침 (파이프라인 실행 후)
  1. 계절성 배너 확인 → 시장 스탠스 결정
  2. 액션 카드 확인 → 즉각 매수/매도 후보 파악
  3. 트레이드 노트 → 손절 시그널 체크

주 1회
  4. Top10 7d 뷰 → 순위 변동 추세 파악
  5. 재무비율 → 보유 종목 건전성 재확인

월 1회 또는 계절성 변환 시
  6. 주목 섹터 재검토 → 포트폴리오 섹터 구성 조정
  7. 자산배분 현황 → 주식/현금/금 비중 리밸런싱
```
""")

# ─── 계절성 시그널 배너 ───────────────────────────────────────────────────────

_month = date.today().month
_is_offensive = _month in [11, 12, 1, 2, 3, 4]
if _is_offensive:
    st.success(
        f"**공격 시즌 ({_month}월)** — 통계적으로 수익률이 좋은 11~4월 구간입니다. "
        "주식 비중 유지/확대를 검토하세요.",
        icon="🟢",
    )
else:
    st.warning(
        f"**방어 시즌 ({_month}월)** — 통계적으로 수익률이 낮은 5~10월 구간입니다. "
        "비중 축소 및 현금 확보를 검토하세요.",
        icon="🟡",
    )

# ─── 자산배분 현황 ────────────────────────────────────────────────────────────

with st.expander("📊 자산배분 현황 (권고: 주식 35% · 현금 50% · 금 15%)", expanded=False):
    _saved_alloc = load_alloc()
    _ac1, _ac2, _ac3 = st.columns(3)
    _stock_in = _ac1.number_input("주식 (%)", 0, 100, _saved_alloc.get("stock", 35), key="alloc_stock")
    _cash_in  = _ac2.number_input("현금 (%)", 0, 100, _saved_alloc.get("cash",  50), key="alloc_cash")
    _gold_in  = _ac3.number_input("금 (%)",   0, 100, _saved_alloc.get("gold",  15), key="alloc_gold")
    _total    = _stock_in + _cash_in + _gold_in
    if _total != 100:
        st.warning(f"합계가 {_total}%입니다. 100%가 되도록 조정하세요.")
    else:
        save_alloc({"stock": _stock_in, "cash": _cash_in, "gold": _gold_in})
        TARGET = {"주식": 35, "현금": 50, "금": 15}
        CURRENT = {"주식": _stock_in, "현금": _cash_in, "금": _gold_in}
        _g1, _g2, _g3 = st.columns(3)
        for _col, (_label, _cur) in zip([_g1, _g2, _g3], CURRENT.items()):
            _tgt  = TARGET[_label]
            _diff = _cur - _tgt
            _diff_str = f"{_diff:+d}%p"
            _col.metric(_label, f"{_cur}%", _diff_str,
                        delta_color="inverse" if _label == "주식" and not _is_offensive else "normal")

# ─── 종목 검색 결과는 퀀트 소형주 스크리너 섹션 아래에 표시됩니다 ──────────────────
    st.divider()

# ─── 알림 배너 ───────────────────────────────────────────────────────────────

if all_new or all_removed:
    b1, b2 = st.columns(2)
    with b1:
        if all_new:
            badges = "  ".join(f"`🆕 {n} ({s})`" for n, s in all_new[:6])
            extra = f"  …외 {len(all_new) - 6}개" if len(all_new) > 6 else ""
            st.success(f"**오늘 신규편입 {len(all_new)}종목**  {badges}{extra}")
    with b2:
        if all_removed:
            badges = "  ".join(f"`🔴 {n} ({s})`" for n, s in all_removed[:6])
            extra = f"  …외 {len(all_removed) - 6}개" if len(all_removed) > 6 else ""
            st.error(f"**오늘 제외 {len(all_removed)}종목**  {badges}{extra}")

# ─── KPI 요약 카드 ────────────────────────────────────────────────────────────

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("모니터링 섹터", f"{len(rankings_data)}개")
k2.metric("주목 섹터 ⭐", f"{highlighted_count}개")
k3.metric("오늘 신규편입", f"{len(all_new)}종목")
k4.metric("오늘 제외", f"{len(all_removed)}종목")
k5.metric("재무비율 산출", f"{per_ok}종목", help="PER 계산 가능 종목 수")

st.divider()

# ─── SECTION 0: 오늘의 액션 카드 ─────────────────────────────────────────────

if all_new or all_removed:
    st.markdown('<a id="action-cards"></a>', unsafe_allow_html=True)
    st.header("📋 오늘의 액션 카드")
    today_str  = date.today().isoformat()
    action_log = load_action_log()
    today_log  = action_log.get(today_str, {})

    c_buy, c_sell = st.columns(2)

    with c_buy:
        st.markdown("### 🟢 매수 검토")
        if all_new:
            for name, sector in all_new:
                ticker = next(
                    (it["ticker"]
                     for s, v in rankings_data.items() if s == sector
                     for it in v.get("top10", []) if it.get("name") == name),
                    None,
                )
                r        = ratios_data.get(ticker, {}) if ticker else {}
                per_str  = f"PER {r['per']:.1f}"          if r.get("per")            else "PER N/A"
                roe_str  = f"ROE {r['roe']:.1f}%"         if r.get("roe") is not None else "ROE N/A"
                debt_str = f"부채비율 {r['debt_ratio']:.1f}%" if r.get("debt_ratio")  else ""

                status = today_log.get(name, {}).get("status", "")
                icon   = "✅" if status == "완료" else ("⏭" if status == "패스" else "⬜")
                with st.expander(f"{icon} **{name}** ({sector})", expanded=False):
                    st.caption(f"{per_str} · {roe_str} · {debt_str}")
                    col1, col2 = st.columns(2)
                    if col1.button("검토 완료 ✅", key=f"buy_done_{name}"):
                        today_log[name] = {"action": "매수검토", "status": "완료"}
                        action_log[today_str] = today_log
                        save_action_log(action_log)
                        st.rerun()
                    if col2.button("다음 기회로 ⏭", key=f"buy_skip_{name}"):
                        today_log[name] = {"action": "매수검토", "status": "패스"}
                        action_log[today_str] = today_log
                        save_action_log(action_log)
                        st.rerun()
        else:
            st.caption("오늘 신규편입 종목 없음")

    with c_sell:
        st.markdown("### 🔴 매도 검토")
        if all_removed:
            for name, sector in all_removed:
                log_key = f"sell_{name}"
                status  = today_log.get(log_key, {}).get("status", "")
                icon    = "✅" if status == "완료" else ("🔒" if status == "유지" else "⬜")
                with st.expander(f"{icon} **{name}** ({sector} 이탈)", expanded=False):
                    col1, col2 = st.columns(2)
                    if col1.button("검토 완료 ✅", key=f"sell_done_{name}"):
                        today_log[log_key] = {"action": "매도검토", "status": "완료"}
                        action_log[today_str] = today_log
                        save_action_log(action_log)
                        st.rerun()
                    if col2.button("유지 🔒", key=f"sell_hold_{name}"):
                        today_log[log_key] = {"action": "매도검토", "status": "유지"}
                        action_log[today_str] = today_log
                        save_action_log(action_log)
                        st.rerun()
        else:
            st.caption("오늘 이탈 종목 없음")

    st.divider()

# ─── SECTION 1: 향후 6개월 주목 섹터 ─────────────────────────────────────────

st.markdown('<a id="highlight"></a>', unsafe_allow_html=True)
st.header("🌟 향후 6개월 주목 섹터")

if themes_data:
    highlighted = {s: t for s, t in themes_data.items() if t.get("highlight")}

    if highlighted:
        h_cols = st.columns(min(len(highlighted), 4))
        for i, (sector, t) in enumerate(highlighted.items()):
            with h_cols[i % min(len(highlighted), 4)]:
                sc_val  = t.get("composite_6m", 0) or 0
                sc_prev = sector_scores.get(sector, {}).get("composite_score", 0) or 0
                sent    = t.get("sentiment_score", 0) or 0
                art_cnt = t.get("article_count", 0)
                krx_match = next(
                    (k for k in rankings_data if KRX_TO_NEWS.get(k, k) == sector or k == sector),
                    None,
                )
                st.success(f"**⭐ {sector}**")
                st.progress(
                    min(1.0, max(0.0, sc_val)),
                    text=f"6m 점수: {sc_val:.3f}  |  단기: {sc_prev:.3f}",
                )
                _hc1, _hc2 = st.columns(2)
                _hc1.metric("뉴스 감성", "긍정" if sent > 0.3 else ("부정" if sent < -0.2 else "중립"))
                _hc2.metric("기사수", f"{art_cnt}건")
                if krx_match and st.button(
                    "📊 Top10 바로 보기", key=f"jump_top10_{sector}", use_container_width=True
                ):
                    st.session_state["focus_krx_sector"] = krx_match
                    st.rerun()

    # 전 섹터 수평 바 차트
    sectors_sorted = sorted(
        themes_data.items(),
        key=lambda x: x[1].get("composite_6m") or 0,
        reverse=True,
    )
    bar_names  = [f"⭐ {s}" if themes_data[s].get("highlight") else s for s, _ in sectors_sorted]
    bar_scores = [v.get("composite_6m") or 0 for _, v in sectors_sorted]

    fig_bar = px.bar(
        x=bar_scores,
        y=bar_names,
        orientation="h",
        color=bar_scores,
        color_continuous_scale="RdYlGn",
        title="섹터별 6개월 주목도 종합 점수 (composite_6m)",
        labels={"x": "6m 종합 점수", "y": "섹터"},
    )
    fig_bar.update_layout(
        height=max(350, len(sectors_sorted) * 32),
        coloraxis_showscale=False,
        yaxis={"autorange": "reversed"},
        margin={"l": 10, "r": 60, "t": 45, "b": 20},
    )
    fig_bar.update_traces(
        text=[f"{s:.3f}" for s in bar_scores],
        textposition="outside",
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # 섹터 종합 히트맵
    with st.expander("🗺 섹터 종합 히트맵 — 한 눈에 보기", expanded=False):
        import numpy as _np
        _hm_sectors = list(themes_data.keys())
        _hm_col_labels = ["6m 점수", "뉴스 감성", "전망 비율", "모멘텀(등락)", "상승 비율"]
        _hm_matrix: list[list[float]] = []
        for _hs in _hm_sectors:
            _ht = themes_data[_hs]
            _hsc = sector_scores.get(_hs, {})
            _hm_matrix.append([
                float(_ht.get("composite_6m", 0) or 0),
                float(_ht.get("sentiment_score", 0) or 0),
                float(_ht.get("forward_ratio", 0) or 0),
                float(_hsc.get("avg_change_rate", 0) or 0),
                float((_hsc.get("advancing_ratio", 0) or 0)),
            ])
        if _hm_matrix:
            _hm_np = _np.array(_hm_matrix, dtype=float)
            _hm_norm = _np.zeros_like(_hm_np)
            for _j in range(_hm_np.shape[1]):
                _col_v = _hm_np[:, _j]
                _mn, _mx = _col_v.min(), _col_v.max()
                _hm_norm[:, _j] = (_col_v - _mn) / (_mx - _mn) if _mx > _mn else 0.5
            _sector_labels_hm = [
                ("⭐ " if themes_data[_hs].get("highlight") else "") + _hs
                for _hs in _hm_sectors
            ]
            _text_hm = [
                [f"{_hm_matrix[_i][_j]:.2f}" for _j in range(len(_hm_col_labels))]
                for _i in range(len(_hm_sectors))
            ]
            _fig_hm = go.Figure(data=go.Heatmap(
                z=_hm_norm.tolist(),
                x=_hm_col_labels,
                y=_sector_labels_hm,
                colorscale="RdYlGn",
                zmin=0, zmax=1,
                text=_text_hm,
                texttemplate="%{text}",
                textfont={"size": 10},
                hovertemplate="%{y} | %{x}<br>값: %{text}<br>강도: %{z:.2f}<extra></extra>",
            ))
            _fig_hm.update_layout(
                height=max(320, len(_hm_sectors) * 34),
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                xaxis_side="top",
            )
            st.plotly_chart(_fig_hm, use_container_width=True)
            st.caption("각 셀 = 열 내 상대 강도 (0→1, 초록=강함). 실제 값은 셀 안 숫자. ⭐ = 6개월 주목 섹터")

    # 섹터별 상세 expander
    st.subheader("섹터별 상세 분석 & 뉴스")

    all_theme_sectors = sorted(
        themes_data.keys(),
        key=lambda s: themes_data[s].get("composite_6m") or 0,
        reverse=True,
    )

    for sector in all_theme_sectors:
        t = themes_data[sector]
        is_hl = t.get("highlight", False)
        sc6m = t.get("composite_6m", 0) or 0
        badge = "⭐ " if is_hl else ""
        label = f"{badge}{sector}   6m점수: {sc6m:.3f}  |  기사 {t.get('article_count', 0)}건"

        with st.expander(label, expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            sent = t.get("sentiment_score", 0) or 0
            fwd  = t.get("forward_ratio", 0) or 0
            adv  = t.get("advancing_ratio")
            chg  = t.get("avg_change_rate")
            c1.metric("뉴스 감성", f"{'긍정' if sent > 0.3 else '부정' if sent < -0.2 else '중립'} ({sent:+.2f})")
            c2.metric("전망 언급 비율", f"{fwd * 100:.0f}%  ({t.get('forward_keyword_hits', 0)}건)")
            c3.metric("주가 모멘텀", f"{chg:+.2f}%" if chg else "-")
            c4.metric("상승 종목 비율", f"{adv:.0f}%" if adv else "-")

            kws = t.get("top_keywords", [])
            if kws:
                st.markdown(f"**📌 핵심 이슈:** `{'`  `'.join(kws)}`")

            if is_hl:
                fwd_hits  = t.get("forward_keyword_hits", 0)
                risk_hits = t.get("risk_keyword_hits", 0)
                if fwd_hits > risk_hits * 2:
                    st.success(
                        f"**호재 요인**: 전망 키워드 {fwd_hits}건 "
                        f"(위험 대비 {fwd_hits / max(risk_hits, 1):.1f}배) "
                        f"— 기사 내 성장·수혜·확대 언급이 두드러짐"
                    )
                else:
                    st.info(f"전망 키워드 {fwd_hits}건 / 위험 키워드 {risk_hits}건 — 혼조세")

            articles = news_preprocessed.get(sector, {}).get("articles", [])
            if articles:
                st.markdown("**📰 관련 뉴스**")
                for art in articles:
                    title     = art.get("title", "")
                    url       = art.get("url", "")
                    sentences = art.get("key_sentences", "")
                    if isinstance(sentences, list):
                        sentences = " ".join(sentences)
                    summary = str(sentences)[:80] + "…" if len(str(sentences)) > 80 else str(sentences)
                    if url:
                        st.markdown(f"- [{title}]({url})")
                    else:
                        st.markdown(f"- {title}")
                    if summary.strip():
                        st.caption(f"  ↳ {summary}")

    with st.expander("📋 전체 섹터 점수 테이블", expanded=False):
        score_rows = []
        for s in all_theme_sectors:
            t  = themes_data[s]
            sc = sector_scores.get(s, {})
            score_rows.append({
                "섹터":          ("⭐ " if t.get("highlight") else "") + s,
                "기사":          t.get("article_count", 0),
                "핵심 키워드":   ", ".join(t.get("top_keywords", [])[:4]),
                "전망 언급":     t.get("forward_keyword_hits", 0),
                "감성점수":      fmt(t.get("sentiment_score"), "", 2),
                "평균등락률(%)": fmt(sc.get("avg_change_rate"), "", 2),
                "상승비율(%)":   fmt(sc.get("advancing_ratio"), "", 1),
                "매출성장률(%)": fmt(sc.get("avg_revenue_growth"), "", 1),
                "6m 점수":       fmt(t.get("composite_6m"), "", 3),
            })
        df_th = pd.DataFrame(score_rows)

        def _hl_row(row):
            return (["background-color:#fff9c4;font-weight:bold"] * len(row)
                    if str(row["섹터"]).startswith("⭐") else [""] * len(row))

        st.caption("6m점수 = 감성(30%) + 전망비율(40%) + 모멘텀(30%)")
        st.dataframe(df_th.style.apply(_hl_row, axis=1), use_container_width=True, hide_index=True)

st.divider()

# ─── SECTION 2: 섹터별 시총 Top 10 변동 ──────────────────────────────────────

st.markdown('<a id="top10"></a>', unsafe_allow_html=True)
st.header("📊 섹터별 시총 Top 10 — 순위 변동")

period_col = PERIOD_TO_COL.get(compare_period, "1일전")
st.caption(
    f"🆕 신규편입(파랑) | 🔺 상승(초록) | 🔻 하락(빨강) | ➖ 유지  /  "
    f"⭐ = 6개월 주목 섹터  /  비교 기준: **{compare_period}** (사이드바에서 변경)"
)

if rankings_data:
    _all_sorted_sectors = sorted(
        rankings_data.keys(),
        key=lambda s: sector_composite_score(s, sector_scores, themes_data),
        reverse=True,
    )

    _focus_sector = st.session_state.get("focus_krx_sector")
    if _focus_sector and _focus_sector in rankings_data:
        _fc1, _fc2 = st.columns([5, 1])
        _fc1.info(f"📍 **{krx_display_name(_focus_sector)}** 섹터만 표시 중 — 주목 섹터 카드에서 선택됨")
        if _fc2.button("전체 보기 ✕", key="clear_focus_sector"):
            del st.session_state["focus_krx_sector"]
            st.rerun()
        sorted_sectors = [_focus_sector]
    else:
        sorted_sectors = _all_sorted_sectors

    for krx_sector in sorted_sectors:
        v      = rankings_data[krx_sector]
        top10  = v.get("top10", [])
        ch     = v.get("changes", {}).get(compare_period, {})
        has_ch = bool(ch.get("new_entries") or ch.get("removed"))
        hl     = is_highlighted(krx_sector, themes_data)
        score  = sector_composite_score(krx_sector, sector_scores, themes_data)
        display = krx_display_name(krx_sector)

        badge      = "⭐ " if hl else ("🔔 " if has_ch else "")
        score_str  = f"  6m:{score:.3f}" if score > 0 else ""
        label      = f"{badge}{display}{score_str}  ({len(top10)}개 종목)"

        with st.expander(label, expanded=False):
            if has_ch:
                new_e    = ch.get("new_entries", [])
                removed_e = ch.get("removed", [])
                top10_map = {it["ticker"]: it.get("name", it["ticker"]) for it in top10}
                if new_e:
                    ns = ", ".join(f"{top10_map.get(t, t)}({t})" for t in new_e)
                    st.success(f"🆕 신규편입 ({compare_period}): {ns}")
                if removed_e:
                    rs = ", ".join(market_data_raw.get(t, {}).get("name", t) for t in removed_e)
                    st.error(f"🔴 제외 ({compare_period}): {rs}")

            rows = []
            for item in top10:
                rows.append({
                    "순위":       item["rank"],
                    "종목코드":   item["ticker"],
                    "종목명":     item["name"],
                    "시총(억)":   fmt(item.get("market_cap_억"), "", 0),
                    "현재가":     fmt(item.get("close"), "원", 0),
                    "등락률(%)":  fmt(item.get("change_rate")),
                    "1일전":      item.get("변동_1d", "-"),
                    "7일전":      item.get("변동_7d", "-"),
                    "15일전":     item.get("변동_15d", "-"),
                    "30일전":     item.get("변동_30d", "-"),
                })

            df_r = pd.DataFrame(rows)

            def _style_rank(val):
                s = str(val)
                if "신규편입" in s:
                    return "background-color:#cce5ff; color:#004085; font-weight:bold"
                if "🔺" in s:
                    return "background-color:#d4edda; color:#155724; font-weight:bold"
                if "🔻" in s:
                    return "background-color:#f8d7da; color:#721c24"
                return ""

            def _style_chg(val):
                try:
                    v = float(str(val).replace("+", "").replace(",", ""))
                    if v >= 3:   return "background-color:#15803d; color:white; font-weight:bold"
                    if v >= 1:   return "background-color:#dcfce7; color:#15803d; font-weight:bold"
                    if v > 0:    return "background-color:#f0fdf4; color:#166534"
                    if v <= -3:  return "background-color:#b91c1c; color:white; font-weight:bold"
                    if v <= -1:  return "background-color:#fde8e8; color:#b91c1c; font-weight:bold"
                    if v < 0:    return "background-color:#fff5f5; color:#991b1b"
                except (ValueError, TypeError):
                    pass
                return ""

            styled_r = (
                df_r.style
                .applymap(_style_rank, subset=[period_col])
                .applymap(_style_chg, subset=["등락률(%)"])
            )
            _top10_ev = st.dataframe(
                styled_r, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                key=f"top10_{krx_sector}",
            )
            _top10_sel = (_top10_ev.selection.rows or []) if hasattr(_top10_ev, "selection") else []
            if _top10_sel and _top10_sel[0] < len(top10):
                _top10_item = top10[_top10_sel[0]]
                with st.expander(f"📈 {_top10_item['name']} ({_top10_item['ticker']}) 상세 차트", expanded=True):
                    _render_stock_chart(_top10_item["ticker"], _top10_item["name"])

st.divider()

# ─── SECTION 3: 재무비율 분석 ─────────────────────────────────────────────────

st.markdown('<a id="ratios"></a>', unsafe_allow_html=True)
st.header("💹 재무비율 분석")

sample_period = next((v.get("period") for v in ratios_data.values() if v.get("period")), None)
st.caption(
    f"📁 DART 기준: {decode_period(sample_period)}  |  "
    f"🟢초록=섹터 대비 좋음  🔴빨강=나쁨  |  섹터 필터: 사이드바에서 변경"
)

if rankings_data and ratios_data:
    if selected_opt == "⭐ 주목 섹터 전체":
        target_krx = hl_krx
    elif selected_opt == "── 전체 ──":
        target_krx = all_krx
    else:
        clean = selected_opt.replace("⭐ ", "")
        target_krx = [s for s in all_krx if krx_display_name(s) == clean]

    LOWER_BETTER = {"PER 주가수익비율", "PBR 주가순자산비율", "부채비율(%)"}
    HIGHER_BETTER = {"ROE(%) 자기자본이익률", "매출성장률(%)"}

    display_rows = []
    ticker_list: list[tuple[str, str, str]] = []  # (ticker, name, krx_sector)

    for krx_sector in target_krx:
        avg = sector_avgs.get(krx_sector, {})
        for item in rankings_data.get(krx_sector, {}).get("top10", []):
            t = item["ticker"]
            r = ratios_data.get(t, {})
            name = r.get("corp_name") or item["name"]
            ticker_list.append((t, name, krx_sector))
            display_rows.append({
                "섹터":                            krx_display_name(krx_sector),
                "순위":                            item["rank"],
                "종목코드":                        t,
                "종목명":                          name,
                "PER 주가수익비율":                delta_badge(r.get("per"),            avg.get("per"),            lower_better=True),
                "ROE(%) 자기자본이익률":           delta_badge(r.get("roe"),            avg.get("roe"),            lower_better=False),
                "PBR 주가순자산비율":              delta_badge(r.get("pbr"),            avg.get("pbr"),            lower_better=True),
                "EPS(원) 주당순이익":              fmt(r.get("eps"), "", 0),
                "EPS성장률(%) 주당순이익증가율":   fmt(r.get("eps_growth")),
                "매출성장률(%)":                   delta_badge(r.get("revenue_growth"), avg.get("revenue_growth"), lower_better=False),
                "부채비율(%)":                     delta_badge(r.get("debt_ratio"),     avg.get("debt_ratio"),     lower_better=True),
                "공시경고":                        "⚠️" if r.get("disclosure_warning") else "",
                "미계산":                          "; ".join(r.get("missing_fields", []))[:50] if r.get("missing_fields") else "",
            })

    # 단일 섹터 평균 메트릭
    if len(target_krx) == 1:
        krx = target_krx[0]
        avg = sector_avgs.get(krx, {})
        if any(avg.values()):
            st.markdown(f"**{krx_display_name(krx)} 섹터 평균** (전체 종목 기준)")
            ac1, ac2, ac3, ac4, ac5 = st.columns(5)
            ac1.metric("평균 PER",        fmt(avg.get("per")))
            ac2.metric("평균 ROE(%)",      fmt(avg.get("roe")))
            ac3.metric("평균 PBR",        fmt(avg.get("pbr")))
            ac4.metric("평균 부채비율(%)", fmt(avg.get("debt_ratio")))
            ac5.metric("평균 매출성장(%)", fmt(avg.get("revenue_growth")))
            st.caption("🟢=평균 대비 좋음  🔴=평균 대비 나쁨  |  PER·PBR·부채비율↓ 좋음, ROE·매출성장↑ 좋음")

    if display_rows:
        df_fin = pd.DataFrame(display_rows)

        tab_table, tab_chart, tab_radar = st.tabs(["📋 테이블", "📊 막대 차트", "🕸 레이더 차트"])

        with tab_table:
            def _style_fin(row):
                if row.get("공시경고") == "⚠️":
                    return ["background-color:#fff3cd"] * len(row)
                styles = [""] * len(row)
                for i, col in enumerate(row.index):
                    val = str(row.iloc[i])
                    if col in LOWER_BETTER:
                        if "(+" in val:
                            styles[i] = "background-color:#fde8e8; color:#b91c1c"
                        elif "(-" in val:
                            styles[i] = "background-color:#dcfce7; color:#15803d"
                    elif col in HIGHER_BETTER:
                        if "(+" in val:
                            styles[i] = "background-color:#dcfce7; color:#15803d"
                        elif "(-" in val:
                            styles[i] = "background-color:#fde8e8; color:#b91c1c"
                return styles

            st.dataframe(
                df_fin.style.apply(_style_fin, axis=1),
                use_container_width=True,
                hide_index=True,
            )

        with tab_chart:
            chart_df = df_fin.copy()
            for col_name in ["ROE(%) 자기자본이익률", "PBR 주가순자산비율", "PER 주가수익비율"]:
                chart_df[f"{col_name}_n"] = pd.to_numeric(
                    chart_df[col_name].str.extract(r"^([-\d,.]+)")[0].str.replace(",", ""),
                    errors="coerce",
                )

            cc1, cc2 = st.columns(2)
            with cc1:
                roe_col = "ROE(%) 자기자본이익률_n"
                roe_d = chart_df.dropna(subset=[roe_col]).nlargest(15, roe_col)
                if not roe_d.empty:
                    fig = px.bar(roe_d, x="종목명", y=roe_col, color="섹터",
                                 title="ROE 자기자본이익률 상위 15",
                                 labels={roe_col: "ROE(%)"})
                    fig.update_xaxes(tickangle=45)
                    fig.update_layout(height=420)
                    st.plotly_chart(fig, use_container_width=True)
            with cc2:
                pbr_col = "PBR 주가순자산비율_n"
                pbr_d = chart_df.dropna(subset=[pbr_col, roe_col])
                if not pbr_d.empty:
                    fig = px.scatter(pbr_d, x=pbr_col, y=roe_col,
                                     text="종목명", color="섹터",
                                     title="PBR vs ROE (섹터별)",
                                     labels={pbr_col: "PBR", roe_col: "ROE(%)"})
                    fig.update_traces(textposition="top center")
                    fig.update_layout(height=420)
                    st.plotly_chart(fig, use_container_width=True)

        with tab_radar:
            if len(target_krx) == 1:
                krx_r = target_krx[0]
                avg_r = sector_avgs.get(krx_r, {})

                stock_opts   = [(t, n) for t, n, _ in ticker_list]
                stock_labels = [f"{t} {n}" for t, n in stock_opts]

                selected_labels = st.multiselect(
                    "종목 선택 (최대 5개 비교)",
                    stock_labels,
                    default=stock_labels[:3],
                    help="레이더 차트에 표시할 종목을 선택하세요",
                )
                if len(selected_labels) > 5:
                    st.warning("최대 5개까지만 표시합니다.")
                    selected_labels = selected_labels[:5]

                axes = ["PER↓저평가", "ROE↑수익성", "PBR↓저평가",
                        "EPS성장률↑", "매출성장률↑", "부채비율↓안전"]

                metrics_info = [
                    ("per",            True),
                    ("roe",            False),
                    ("pbr",            True),
                    ("eps_growth",     False),
                    ("revenue_growth", False),
                    ("debt_ratio",     True),
                ]

                def _get_range(metric):
                    caps = {"per": 300, "pbr": 50, "debt_ratio": 5000}
                    vals = []
                    for t_v, _, _ in ticker_list:
                        v_v = ratios_data.get(t_v, {}).get(metric)
                        if v_v is not None:
                            if metric in caps and abs(v_v) > caps[metric]:
                                continue
                            vals.append(v_v)
                    if len(vals) < 2:
                        return None, None
                    return min(vals), max(vals)

                ranges = {m: _get_range(m) for m, _ in metrics_info}

                def _ticker_radar(ticker_id):
                    r_t = ratios_data.get(ticker_id, {})
                    return [
                        _norm_val(r_t.get(m), *ranges.get(m, (None, None)), invert=inv)
                        for m, inv in metrics_info
                    ]

                avg_radar = [
                    _norm_val(avg_r.get(m), *ranges.get(m, (None, None)), invert=inv)
                    for m, inv in metrics_info
                ]

                fig_radar = go.Figure()
                fig_radar.add_trace(go.Scatterpolar(
                    r=avg_radar + [avg_radar[0]],
                    theta=axes + [axes[0]],
                    fill="toself",
                    fillcolor="rgba(128,128,128,0.12)",
                    line={"color": "gray", "dash": "dash", "width": 2},
                    name=f"섹터 평균 ({krx_display_name(krx_r)})",
                ))

                hex_to_rgba = {
                    "#3b82f6": "rgba(59,130,246,0.18)",
                    "#ef4444": "rgba(239,68,68,0.18)",
                    "#22c55e": "rgba(34,197,94,0.18)",
                    "#f59e0b": "rgba(245,158,11,0.18)",
                    "#8b5cf6": "rgba(139,92,246,0.18)",
                }

                for idx, label in enumerate(selected_labels):
                    parts  = label.split(" ", 1)
                    t_id   = parts[0]
                    t_name = parts[1] if len(parts) > 1 else label
                    r_vals = _ticker_radar(t_id)
                    color  = RADAR_COLORS[idx % len(RADAR_COLORS)]
                    fill   = hex_to_rgba.get(color, "rgba(59,130,246,0.18)")
                    fig_radar.add_trace(go.Scatterpolar(
                        r=r_vals + [r_vals[0]],
                        theta=axes + [axes[0]],
                        fill="toself",
                        fillcolor=fill,
                        line={"color": color, "width": 2},
                        name=t_name,
                    ))

                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(visible=True, range=[0, 1], showticklabels=False),
                        angularaxis=dict(direction="clockwise"),
                    ),
                    height=520,
                    title=f"{krx_display_name(krx_r)} — 종목별 재무비율 레이더",
                    legend=dict(orientation="h", yanchor="bottom", y=-0.28),
                )
                st.plotly_chart(fig_radar, use_container_width=True)
                st.caption(
                    "각 축: 0~1 정규화, **클수록 좋음**으로 통일 "
                    "(PER↓·PBR↓·부채비율↓ → 1에 가까울수록 저평가·재무건전).  "
                    "None 값 = 0.5 중립 처리."
                )
            else:
                st.info("💡 레이더 차트는 **단일 섹터** 선택 시에만 사용할 수 있습니다. 사이드바에서 섹터를 하나 선택해 주세요.")

# ─── 재무지표 가이드 ──────────────────────────────────────────────────────────

with st.expander("📖 재무지표 읽는 법 — 각 숫자가 의미하는 것", expanded=False):
    st.markdown("""
### 재무비율 완전 가이드

> 숫자 뒤 괄호 **(+N%)** 는 **섹터 대표값보다 N% 높다**는 뜻, **(-N%)** 는 낮다는 뜻입니다.
> PER·PBR·부채비율은 낮을수록, ROE·매출성장률은 높을수록 일반적으로 좋습니다.

---

#### 💰 PER — 주가수익비율 (Price-to-Earnings Ratio)

**계산식** : `주가 ÷ 주당순이익(EPS)`

**쉽게 말하면** : 지금 이 주식이 **1년 순이익의 몇 배** 가격에 팔리고 있는지입니다.
예를 들어 PER 10이면 "지금 주가는 연간 순이익의 10배"라는 뜻.

| PER 범위 | 일반적 해석 |
|---------|-----------|
| 0 ~ 10 | 저평가 가능성 (또는 성장 기대 낮음) |
| 10 ~ 20 | 적정 수준 |
| 20 ~ 50 | 성장 기대 반영 (성장주) |
| 50 이상 | 고평가 주의 (또는 이익 급감) |
| 음수·표시 없음 | 적자 기업 — PER 계산 불가 |

> ⚠️ **섹터별로 기준이 다릅니다.** 제약·바이오는 PER 50 이상도 흔하고, 건설·금융은 10 내외가 보통입니다. 반드시 **(+N%)** 괄호의 **섹터 비교값**을 함께 보세요.

---

#### 📈 ROE — 자기자본이익률 (Return on Equity)

**계산식** : `순이익 ÷ 자기자본 × 100 (%)`

**쉽게 말하면** : 주주가 맡긴 돈으로 **얼마나 효율적으로 돈을 벌었는지**입니다.
ROE 15% = "자기자본 100원으로 15원을 벌었다"

| ROE 범위 | 일반적 해석 |
|---------|-----------|
| 15% 이상 | 우량 기업 (버핏 기준선) |
| 10 ~ 15% | 양호 |
| 0 ~ 10% | 보통 |
| 0% 미만 | 적자 (자기자본 잠식 위험) |

> ⚠️ ROE가 매우 높아도 부채로 레버리지를 극도로 올린 결과일 수 있습니다. **부채비율과 함께** 보세요.

---

#### 📊 PBR — 주가순자산비율 (Price-to-Book Ratio)

**계산식** : `주가 ÷ 주당순자산(BPS)`  ·  BPS = `자기자본 ÷ 발행주식수`

**쉽게 말하면** : 지금 주가가 **회사 장부상 자산 가치의 몇 배**냐는 것입니다.
PBR 0.5 = "주가가 청산가치(자산-부채)의 절반 수준 → 극도의 저평가 가능성"
PBR 5.0 = "장부가의 5배에 팔림 → 브랜드·기술 등 무형자산 반영"

| PBR 범위 | 일반적 해석 |
|---------|-----------|
| 1.0 미만 | 이론적 저평가 (청산가치 이하) |
| 1 ~ 3 | 적정 |
| 3 이상 | 성장 프리미엄 또는 고평가 |

---

#### 🔢 EPS — 주당순이익 (Earnings Per Share)

**계산식** : `순이익 ÷ 발행주식수`  (원 단위)

**쉽게 말하면** : 주식 1주가 1년 동안 **얼마를 벌었는지**입니다.
EPS 2,000원 = "주식 1주당 연간 순이익 2,000원"

---

#### 📉 EPS 성장률 (%)

**계산식** : `(금기 EPS - 전기 EPS) ÷ |전기 EPS| × 100`

성장률이 높으면 고 PER도 정당화될 수 있습니다. (성장주 투자의 핵심 지표)

---

#### 💹 매출 성장률 (%)

**계산식** : `(금기 매출 - 전기 매출) ÷ |전기 매출| × 100`

이익이 없어도 매출이 빠르게 성장하면 미래 이익 창출 가능성이 높다고 봅니다.

---

#### 🏦 부채비율 (%)

**계산식** : `총부채 ÷ 자기자본 × 100`

| 부채비율 | 일반적 해석 |
|---------|-----------|
| 100% 미만 | 재무 안전 |
| 100 ~ 200% | 보통 (업종마다 다름) |
| 200% 이상 | 주의 필요 |
| 음수·표시 없음 | **자본잠식** — 자기자본이 음수 |

---

#### 📌 괄호 안 % 읽는 법

| 표시 | 뜻 | 좋은 경우 |
|-----|-----|---------|
| PER `12.0 (-84%)` | 섹터 중앙값보다 84% 낮음 | ✅ 상대적 저평가 |
| PER `38.0 (+287%)` | 섹터 중앙값보다 287% 높음 | ❌ 상대적 고평가 |
| ROE `18.0 (+240%)` | 섹터 트리밍평균보다 240% 높음 | ✅ 동종 대비 우량 |
| 부채비율 `50 (-64%)` | 섹터 평균보다 64% 낮음 | ✅ 재무 건전 |

> 섹터 대표값: **PER·PBR은 중앙값** (이상치 제거), **ROE·부채비율은 상하 10% 제거 트리밍 평균**

#### 🕸 레이더 차트 읽는 법

레이더 차트의 각 꼭짓점은 **클수록 좋음**으로 통일 정규화되어 있습니다:
- **PER↓ · PBR↓ · 부채비율↓** : 낮을수록 1에 가까움 (저평가·재무건전)
- **ROE↑ · EPS성장률↑ · 매출성장률↑** : 높을수록 1에 가까움 (수익성·성장성)

회색 점선이 섹터 평균, 색선이 개별 종목입니다.
""")

st.divider()

# ─── 공통 테이블·차트 헬퍼 ────────────────────────────────────────────────────────

def _qf_n(v, d=1):
    if v is None or (isinstance(v, float) and v != v): return "-"
    r = round(float(v), d)
    return str(int(r)) if r == int(r) else str(r)

def _chg_fmt_n(v) -> str:
    if v is None or (isinstance(v, float) and v != v): return "-"
    return f"{float(v):+.2f}%"

def _period_chg(c_near, c_far) -> float | None:
    """누적 등락률 두 개로 구간 등락률 계산.
    c_near: 가까운 시점 누적% (예: chg_7d), c_far: 먼 시점 누적% (예: chg_15d)
    반환: c_far 시점 → c_near 시점 구간 등락률
    """
    if c_near is None or c_far is None: return None
    if isinstance(c_near, float) and c_near != c_near: return None
    if isinstance(c_far,  float) and c_far  != c_far:  return None
    try:
        return round(((1 + c_far / 100) / (1 + c_near / 100) - 1) * 100, 2)
    except (ZeroDivisionError, TypeError):
        return None

def _build_stock_rows(tickers: list[str]) -> list[dict]:
    rows = []
    for tk in tickers:
        if not market_data_raw:
            break
        mkt = market_data_raw.get(tk, {})
        r   = (ratios_data or {}).get(tk, {})
        rows.append({
            "name":           mkt.get("name", tk),
            "ticker":         tk,
            "sector":         mkt.get("sector", "-"),
            "market_cap_억":  mkt.get("market_cap", 0) // 100_000_000,
            "close":          mkt.get("close", 0),
            "chg_1d":         mkt.get("change_rate"),
            "chg_7d":         None,
            "chg_15d":        None,
            "chg_30d":        None,
            "per":            r.get("per"),
            "pbr":            r.get("pbr"),
            "eps_growth":     r.get("eps_growth"),
            "revenue_growth": r.get("revenue_growth"),
            "roe":            r.get("roe"),
        })
    if rows and market_data_raw:
        rows = enrich_price_changes(rows, market_data_raw)
    return rows

def _render_stock_chart(ticker: str, name: str, days: int = 60) -> None:
    """캔들스틱 차트. data/historical CSV → pykrx 순으로 시도."""
    from datetime import timedelta
    hist_path = BASE_DIR / "data" / "historical" / f"{ticker}.csv"
    df_c = None
    if hist_path.exists():
        try:
            df_c = pd.read_csv(hist_path, parse_dates=["Date"])
            df_c = df_c.sort_values("Date").tail(days).reset_index(drop=True)
        except Exception:
            df_c = None
    if df_c is None or df_c.empty:
        try:
            from pykrx import stock as _ks
            _end = datetime.now()
            _start = _end - timedelta(days=days + 25)
            _raw = _ks.get_market_ohlcv_by_date(_start.strftime("%Y%m%d"), _end.strftime("%Y%m%d"), ticker)
            if _raw is not None and not _raw.empty:
                _raw = _raw.reset_index()
                _raw.columns = ["Date" if c == "날짜" else c for c in _raw.columns]
                _raw = _raw.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
                df_c = _raw[["Date"] + [c for c in ["Open","High","Low","Close","Volume"] if c in _raw.columns]].tail(days)
        except Exception:
            df_c = None
    if df_c is None or df_c.empty:
        st.warning(f"{name}({ticker}) 차트 데이터를 불러올 수 없습니다.")
        return
    has_ohlc = all(c in df_c.columns for c in ["Open", "High", "Low", "Close"])
    fig = go.Figure()
    if has_ohlc:
        fig.add_trace(go.Candlestick(
            x=df_c["Date"], open=df_c["Open"], high=df_c["High"],
            low=df_c["Low"], close=df_c["Close"], name=name,
            increasing_line_color="#15803d", decreasing_line_color="#b91c1c",
        ))
    else:
        fig.add_trace(go.Scatter(x=df_c["Date"], y=df_c["Close"], mode="lines", name=name,
                                 line=dict(color="#1e40af", width=2)))
    if "Volume" in df_c.columns:
        fig.add_trace(go.Bar(x=df_c["Date"], y=df_c["Volume"], name="거래량",
                             yaxis="y2", marker_color="rgba(120,120,220,0.25)"))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                      title="거래량", tickformat=".2s"))
    fig.update_layout(
        title=f"{name} ({ticker}) — 최근 {len(df_c)}거래일",
        xaxis_rangeslider_visible=False,
        height=420, margin=dict(l=10, r=10, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

def _apply_chg_style(styles, col_names, col, raw_val):
    if col not in col_names: return
    ci = col_names.index(col)
    if raw_val is None or (isinstance(raw_val, float) and raw_val != raw_val): return
    v = float(raw_val)
    if   v >=  5.0: styles[ci] = "background-color:#dcfce7;color:#166534;font-weight:bold"
    elif v >=  2.0: styles[ci] = "background-color:#f0fdf4;color:#15803d"
    elif v >=  0.0: styles[ci] = "background-color:#f8fff8;color:#166534"
    elif v >= -2.0: styles[ci] = "background-color:#fff8f8;color:#b91c1c"
    elif v >= -5.0: styles[ci] = "background-color:#fee2e2;color:#b91c1c"
    else:           styles[ci] = "background-color:#fecaca;color:#7f1d1d;font-weight:bold"

def _render_stock_table(rows: list[dict], height: int = 360, table_key: str = "default") -> None:
    """구간별 등락률 + 재무지표 테이블. 행 클릭 시 캔들스틱 차트 표시."""
    if not rows:
        st.info("시장 데이터가 없습니다. 파이프라인을 먼저 실행하세요.")
        return
    _rdf = pd.DataFrame(rows)

    # ── 구간별 등락률 계산 (누적 → 구간 변환) ──────────────────────────────────
    _p_1d   = list(_rdf["chg_1d"])
    _p_1_7  = [_period_chg(r["chg_1d"],  r["chg_7d"])  for r in rows]  # 7일전~어제
    _p_7_15 = [_period_chg(r["chg_7d"],  r["chg_15d"]) for r in rows]  # 15일전~7일전
    _p_15_30= [_period_chg(r["chg_15d"], r["chg_30d"]) for r in rows]  # 30일전~15일전

    _CHG_PERIOD_COLS = ["전일(%)", "1~7일 전(%)", "7~15일 전(%)", "15~30일 전(%)"]
    _chg_period_raw  = dict(zip(_CHG_PERIOD_COLS, [_p_1d, _p_1_7, _p_7_15, _p_15_30]))

    _disp = pd.DataFrame({
        "종목명":        _rdf["name"],
        "티커":          _rdf["ticker"],
        "섹터":          _rdf["sector"],
        "시총(억)":      _rdf["market_cap_억"].apply(lambda x: f"{int(x):,}"),
        "현재가":        _rdf["close"].apply(lambda x: f"₩{int(x):,}" if x else "-"),
        "전일(%)":       [_chg_fmt_n(v) for v in _p_1d],
        "1~7일 전(%)":   [_chg_fmt_n(v) for v in _p_1_7],
        "7~15일 전(%)":  [_chg_fmt_n(v) for v in _p_7_15],
        "15~30일 전(%)": [_chg_fmt_n(v) for v in _p_15_30],
        "PER":           _rdf["per"].apply(lambda x: _qf_n(x)),
        "PBR":           _rdf["pbr"].apply(lambda x: _qf_n(x, 2)),
        "EPS성장(%)":    _rdf["eps_growth"].apply(lambda x: _qf_n(x)),
        "매출성장(%)":   _rdf["revenue_growth"].apply(lambda x: _qf_n(x)),
        "ROE(%)":        _rdf["roe"].apply(lambda x: _qf_n(x)),
    })

    def _style_stock(df_row):
        ri = df_row.name
        st_list = [""] * len(df_row)
        cn = list(df_row.index)
        for col, raw_list in _chg_period_raw.items():
            _apply_chg_style(st_list, cn, col, raw_list[ri] if ri < len(raw_list) else None)
        return st_list

    st.caption("📌 등락률: 각 구간 내 독립 수익률 (전일=어제 대비, 1~7일=7일 전~어제, 7~15일=15일 전~7일 전, 15~30일=30일 전~15일 전)")
    _ev = st.dataframe(
        _disp.style.apply(_style_stock, axis=1),
        use_container_width=True, hide_index=True, height=height,
        on_select="rerun", selection_mode="single-row",
        key=f"tbl_{table_key}",
    )
    sel = (_ev.selection.rows or []) if hasattr(_ev, "selection") else []
    if sel:
        _sr = rows[sel[0]]
        with st.expander(f"📈 {_sr['name']} ({_sr['ticker']}) 상세 차트", expanded=True):
            _render_stock_chart(_sr["ticker"], _sr["name"])

# ─── SECTION 6: 퀀트 소형주 스크리너 ──────────────────────────────────────────

st.markdown('<a id="quant"></a>', unsafe_allow_html=True)
st.header("🔬 퀀트 소형주 스크리너")
st.caption("강환국 퀀트 전략: 시총 하위 20% × 저PER(30%) + 저PBR(20%) + 고EPS성장(25%) + 고매출성장(25%)")

if market_data_raw and ratios_data:
    @st.cache_data(show_spinner="팩터 점수 계산 중...", ttl=3600)
    def _quant_top50(date_key: str) -> list[dict]:
        results = calc_factor_scores(market_data_raw, ratios_data)
        return enrich_price_changes(results, market_data_raw)

    _qresults = _quant_top50(date.today().isoformat())

    if _qresults:
        _qdf = pd.DataFrame(_qresults)
        _qdf.insert(0, "순위", range(1, len(_qdf) + 1))

        # ── 포맷 헬퍼 ────────────────────────────────────────────────────────────
        def _qf(v, d=1):
            if v is None or (isinstance(v, float) and v != v): return "-"
            r = round(float(v), d)
            return str(int(r)) if r == int(r) else str(r)

        def _chg_fmt(v) -> str:
            if v is None or (isinstance(v, float) and v != v): return "-"
            return f"{float(v):+.2f}%"

        # ── 구간별 등락률 계산 ────────────────────────────────────────────────────
        _qp_1d    = _qdf["chg_1d"].tolist()
        _qp_1_7   = [_period_chg(r.get("chg_1d"),  r.get("chg_7d"))  for r in _qresults]
        _qp_7_15  = [_period_chg(r.get("chg_7d"),  r.get("chg_15d")) for r in _qresults]
        _qp_15_30 = [_period_chg(r.get("chg_15d"), r.get("chg_30d")) for r in _qresults]

        _QCHG_COLS = ["전일(%)", "1~7일 전(%)", "7~15일 전(%)", "15~30일 전(%)"]
        _qchg_raw  = dict(zip(_QCHG_COLS, [_qp_1d, _qp_1_7, _qp_7_15, _qp_15_30]))

        # ── 표시 DataFrame ────────────────────────────────────────────────────────
        _display = pd.DataFrame({
            "순위":           _qdf["순위"],
            "종목명":         _qdf["name"],
            "섹터":           _qdf["sector"],
            "시총(억)":       _qdf["market_cap_억"].apply(lambda x: f"{x:,}"),
            "팩터점수":       _qdf["factor_score"].apply(lambda x: f"{x:.4f}"),
            "전일(%)":        [_chg_fmt(v) for v in _qp_1d],
            "1~7일 전(%)":    [_chg_fmt(v) for v in _qp_1_7],
            "7~15일 전(%)":   [_chg_fmt(v) for v in _qp_7_15],
            "15~30일 전(%)":  [_chg_fmt(v) for v in _qp_15_30],
            "PER":            _qdf["per"].apply(lambda x: _qf(x)),
            "PBR":            _qdf["pbr"].apply(lambda x: _qf(x, 2)),
            "EPS성장(%)":     _qdf["eps_growth"].apply(lambda x: _qf(x)),
            "매출성장(%)":    _qdf["revenue_growth"].apply(lambda x: _qf(x)),
            "ROE(%)":         _qdf["roe"].apply(lambda x: _qf(x)),
        })

        def _style_quant(df_row):
            ri = df_row.name
            styles = [""] * len(df_row)
            cn = list(df_row.index)
            if "팩터점수" in cn:
                si = cn.index("팩터점수")
                try:
                    sc = float(df_row.iloc[si])
                    if sc >= 0.75: styles[si] = "background-color:#dcfce7;color:#15803d;font-weight:bold"
                    elif sc >= 0.60: styles[si] = "background-color:#fef9c3;color:#92400e"
                except (ValueError, TypeError): pass
            for col, raw_list in _qchg_raw.items():
                _apply_chg_style(styles, cn, col, raw_list[ri] if ri < len(raw_list) else None)
            return styles

        st.caption("📌 등락률: 각 구간 내 독립 수익률 (전일=어제 대비, 1~7일=7일 전~어제, 7~15일=15일 전~7일 전, 15~30일=30일 전~15일 전)")
        _qev = st.dataframe(
            _display.style.apply(_style_quant, axis=1),
            use_container_width=True, hide_index=True, height=480,
            on_select="rerun", selection_mode="single-row",
            key="tbl_quant",
        )
        _qsel = (_qev.selection.rows or []) if hasattr(_qev, "selection") else []
        if _qsel:
            _qsr = _qresults[_qsel[0]]
            with st.expander(f"📈 {_qsr['name']} ({_qsr['ticker']}) 상세 차트", expanded=True):
                _render_stock_chart(_qsr["ticker"], _qsr["name"])

        # ── 등락률 데이터 설명 ────────────────────────────────────────────────────
        _has_hist = sum(
            1 for r in _qresults
            if r.get("chg_7d") is not None
        )
        _missing = len(_qresults) - _has_hist

        _legend_cols = st.columns(6)
        for _lc, (_label, _bg, _tx) in zip(_legend_cols, [
            ("▲5%+",  "#dcfce7", "#166534"),
            ("▲2~5%", "#f0fdf4", "#15803d"),
            ("▲0~2%", "#f8fff8", "#166534"),
            ("▼0~2%", "#fff8f8", "#b91c1c"),
            ("▼2~5%", "#fee2e2", "#b91c1c"),
            ("▼5%+",  "#fecaca", "#7f1d1d"),
        ]):
            _lc.markdown(
                f'<div style="background:{_bg};color:{_tx};padding:4px 8px;'
                f'border-radius:4px;font-size:12px;text-align:center">{_label}</div>',
                unsafe_allow_html=True,
            )

        _cap_parts = [f"총 {len(_qresults)}개 종목 | 시총 하위 20% 필터 적용"]
        if _missing > 0:
            _cap_parts.append(f"7·15·30일 등락률: {_has_hist}개 조회 완료, {_missing}개 미조회(pykrx 실패 또는 상장 이력 부족)")
        st.caption(" | ".join(_cap_parts))

    else:
        st.info("퀀트 스크리닝 조건에 해당하는 종목이 없습니다.")
else:
    st.info("데이터 없음 — 파이프라인 실행 후 표시됩니다.")

st.divider()

# ─── 종목 검색 결과 (퀀트 스크리너 아래) ─────────────────────────────────────────

_sq = (st.session_state.get("stock_search_query") or "").strip()
if _sq and market_data_raw:
    st.markdown('<a id="search-result"></a>', unsafe_allow_html=True)
    _matches = [
        (tkr, info)
        for tkr, info in market_data_raw.items()
        if _sq.lower() in info.get("name", "").lower() or _sq in tkr
    ]
    st.subheader(f"🔍 '{_sq}' 검색 결과 ({len(_matches)}건)")
    if _matches:
        _search_tickers = [tkr for tkr, _ in _matches[:20]]
        with st.spinner("가격 데이터 조회 중..."):
            _search_rows = _build_stock_rows(_search_tickers)
        _render_stock_table(_search_rows, height=min(560, max(220, len(_search_tickers) * 38 + 60)), table_key="search")

        # ── 트레이드 노트 빠른 추가 ──────────────────────────────────────────────
        _rank_map_s: dict[str, tuple[str, int]] = {}
        for _sec, _sv in rankings_data.items():
            for _it in _sv.get("top10", []):
                _rank_map_s[_it["ticker"]] = (_sec, _it["rank"])

        with st.expander("📒 트레이드 노트에 추가"):
            _sel_name = st.selectbox(
                "종목 선택",
                [info.get("name", tkr) for tkr, info in _matches[:20]],
                key="search_tn_select",
            )
            _sel_tkr  = next((tkr for tkr, info in _matches[:20] if info.get("name", tkr) == _sel_name), None)
            if _sel_tkr:
                _sel_close = market_data_raw.get(_sel_tkr, {}).get("close", 0)
                _existing_notes_s = _load_notes_smart()
                if _sel_name in _existing_notes_s and _existing_notes_s[_sel_name].get("status") == "보유중":
                    st.warning(f"'{_sel_name}'은 이미 트레이드 노트에 보유 중입니다.")
                else:
                    with st.form("search_tn_form", clear_on_submit=True):
                        _sn1, _sn2, _sn3 = st.columns(3)
                        _sn_price = _sn1.number_input("매수가(원)", value=int(_sel_close or 0), min_value=1, step=100)
                        _sn_qty   = _sn2.number_input("수량(주)", value=1, min_value=1)
                        _sn_date  = _sn3.date_input("매수일", value=date.today())
                        _sn_memo  = st.text_input("메모", placeholder="매수 이유...")
                        if st.form_submit_button("💾 저장", use_container_width=True):
                            _existing_notes_s[_sel_name] = {
                                "ticker": _sel_tkr, "buy_price": int(_sn_price),
                                "quantity": int(_sn_qty), "buy_date": _sn_date.isoformat(),
                                "note": _sn_memo.strip(), "peak_price": int(_sn_price),
                                "status": "보유중",
                            }
                            _save_notes_smart(_existing_notes_s)
                            st.success(f"✅ {_sel_name} 트레이드 노트에 추가됨!")
                            st.rerun()
    else:
        st.info("일치하는 종목이 없습니다.")
    st.divider()

# ─── SECTION 7: 트레이드 노트 ─────────────────────────────────────────────────

st.markdown('<a id="trade-note"></a>', unsafe_allow_html=True)
st.header("📒 트레이드 노트")
st.caption("매수가 기준 -10% 손절선 · 고점 기준 -10% 추적 손절선 자동 계산")

_notes = _load_notes_smart()
_tab_hold, _tab_add, _tab_hist = st.tabs(["📌 보유 현황", "➕ 신규 추가", "📋 이력"])

# ── 보유 현황 탭 ──────────────────────────────────────────────────────────────
with _tab_hold:
    _active = {k: v for k, v in _notes.items() if v.get("status") == "보유중"}
    if not _active:
        st.info("보유 중인 종목이 없습니다. '신규 추가' 탭에서 등록하세요.")
    else:
        # ── 포트폴리오 요약 카드 ─────────────────────────────────────────────────
        _total_invest = _total_eval = 0
        _pnl_chart_data: list[dict] = []
        for _nm, _note in _active.items():
            _c = market_data_raw.get(_note.get("ticker", ""), {}).get("close") if market_data_raw else None
            _p = calc_pnl(_note, _c)
            _buy_amt = (_note.get("buy_price", 0) or 0) * (_note.get("quantity", 0) or 0)
            _eval_amt = (_c or (_note.get("buy_price", 0) or 0)) * (_note.get("quantity", 0) or 0)
            _total_invest += _buy_amt
            _total_eval   += _eval_amt
            _pnl_chart_data.append({
                "종목": _nm,
                "손익률": _p["pnl_pct"] or 0.0,
                "평가손익": _p["pnl_amount"] or 0,
                "상태": _p["status_flag"],
            })

        _tot_pnl_amt = _total_eval - _total_invest
        _tot_pnl_pct = (_tot_pnl_amt / _total_invest * 100) if _total_invest else 0.0

        _s1, _s2, _s3, _s4 = st.columns(4)
        _s1.metric("총 투자금액", f"{_total_invest:,.0f}원")
        _s2.metric("총 평가금액", f"{_total_eval:,.0f}원")
        _s3.metric("총 평가손익", f"{_tot_pnl_amt:+,.0f}원",
                   delta_color="normal" if _tot_pnl_amt >= 0 else "inverse")
        _s4.metric("총 수익률", f"{_tot_pnl_pct:+.2f}%",
                   delta_color="normal" if _tot_pnl_pct >= 0 else "inverse")

        # ── P&L 바 차트 ──────────────────────────────────────────────────────────
        if _pnl_chart_data:
            _pnl_df = pd.DataFrame(_pnl_chart_data).sort_values("손익률")
            _bar_colors = [
                "#15803d" if v >= 0 else "#b91c1c" for v in _pnl_df["손익률"]
            ]
            _fig_pnl = go.Figure(go.Bar(
                x=_pnl_df["손익률"],
                y=_pnl_df["종목"],
                orientation="h",
                marker_color=_bar_colors,
                text=[f"{v:+.2f}%" for v in _pnl_df["손익률"]],
                textposition="outside",
            ))
            _fig_pnl.add_vline(x=0, line_color="gray", line_width=1)
            _fig_pnl.update_layout(
                title="종목별 손익률",
                height=max(180, len(_pnl_chart_data) * 48 + 60),
                margin={"l": 10, "r": 70, "t": 40, "b": 20},
                xaxis_title="손익률 (%)",
                showlegend=False,
            )
            st.plotly_chart(_fig_pnl, use_container_width=True)

        st.divider()

        # ── 종목별 상세 ──────────────────────────────────────────────────────────
        _total_pnl = 0
        for _nm, _note in _active.items():
            _cur = market_data_raw.get(_note.get("ticker", ""), {}).get("close") if market_data_raw else None
            _pnl = calc_pnl(_note, _cur)
            _pct_str = f"{_pnl['pnl_pct']:+.2f}%" if _pnl["pnl_pct"] is not None else "?"
            _flag    = _pnl["status_flag"]
            _flag_icon = {"정상": "✅", "손절임박": "⚡", "손절선도달": "🚨", "추적손절도달": "⚠️", "시세없음": "❓"}.get(_flag, "")
            _header  = f"{_flag_icon} **{_nm}** — 손익 {_pct_str}  |  {_flag}"

            with st.expander(_header, expanded=False):
                _edit_key = f"edit_mode_{_nm}"

                if not st.session_state.get(_edit_key):
                    # ── 조회 모드 ─────────────────────────────────────────────
                    _m1, _m2, _m3, _m4, _m5 = st.columns(5)
                    _m1.metric("매수가",    f"{_note['buy_price']:,}원")
                    _m2.metric("현재가",    f"{_cur:,}원" if _cur else "-")
                    _m3.metric("손익률",    _pct_str)
                    _m4.metric("손절가",    f"{_pnl['stop_price']:,}원")
                    _m5.metric("추적손절가", f"{_pnl['trail_price']:,}원")
                    st.caption(
                        f"매수일: {_note.get('buy_date', '-')}  |  "
                        f"수량: {_note.get('quantity', '-')}주  |  "
                        f"고점가: {_note.get('peak_price', _note['buy_price']):,}원"
                    )
                    if _note.get("note"):
                        st.markdown(f"> {_note['note']}")
                    _b1, _b2, _b3 = st.columns(3)
                    if _b1.button("매도 완료", key=f"sell_done_{_nm}"):
                        _notes[_nm]["status"]     = "매도완료"
                        _notes[_nm]["sell_date"]  = date.today().isoformat()
                        _notes[_nm]["sell_price"] = _cur or _note["buy_price"]
                        _save_notes_smart(_notes)
                        st.rerun()
                    if _b2.button("✏️ 수정", key=f"edit_btn_{_nm}"):
                        st.session_state[_edit_key] = True
                        st.rerun()
                    if _b3.button("🗑️ 삭제", key=f"del_{_nm}"):
                        del _notes[_nm]
                        _save_notes_smart(_notes)
                        st.rerun()
                else:
                    # ── 편집 모드 ─────────────────────────────────────────────
                    st.markdown("**✏️ 정보 수정**")
                    with st.form(f"edit_form_{_nm}", clear_on_submit=False):
                        _e1, _e2, _e3 = st.columns(3)
                        _ep = _e1.number_input(
                            "매수가 (원)", min_value=1,
                            value=int(_note.get("buy_price", 0)),
                            step=100, key=f"ep_{_nm}",
                        )
                        _eq = _e2.number_input(
                            "수량 (주)", min_value=1,
                            value=int(_note.get("quantity", 1)),
                            step=1, key=f"eq_{_nm}",
                        )
                        try:
                            _ed_val = date.fromisoformat(_note.get("buy_date", date.today().isoformat()))
                        except ValueError:
                            _ed_val = date.today()
                        _ed = _e3.date_input("매수일", value=_ed_val, key=f"ed_{_nm}")
                        _epeak = st.number_input(
                            "고점가 (원) — 추적손절 기준",
                            min_value=1,
                            value=int(_note.get("peak_price", _note.get("buy_price", 0))),
                            step=100, key=f"epeak_{_nm}",
                        )
                        _ememo = st.text_area(
                            "메모", value=_note.get("note", ""),
                            height=80, key=f"ememo_{_nm}",
                        )
                        _s1, _s2 = st.columns(2)
                        _submitted = _s1.form_submit_button("💾 저장", use_container_width=True)
                        _cancelled = _s2.form_submit_button("취소", use_container_width=True)

                    if _submitted:
                        _notes[_nm]["buy_price"]  = int(_ep)
                        _notes[_nm]["quantity"]   = int(_eq)
                        _notes[_nm]["buy_date"]   = _ed.isoformat()
                        _notes[_nm]["peak_price"] = int(_epeak)
                        _notes[_nm]["note"]       = _ememo.strip()
                        _save_notes_smart(_notes)
                        st.session_state[_edit_key] = False
                        st.rerun()
                    if _cancelled:
                        st.session_state[_edit_key] = False
                        st.rerun()

                if _pnl["pnl_amount"] is not None:
                    _total_pnl += _pnl["pnl_amount"]

        if _active:
            st.metric("총 평가손익", f"{_total_pnl:+,}원",
                      delta_color="normal" if _total_pnl >= 0 else "inverse")

# ── 신규 추가 탭 ──────────────────────────────────────────────────────────────
with _tab_add:
    # ① 검색 (폼 바깥 — 입력할 때마다 실시간 반응)
    _sq_note = st.text_input(
        "종목 검색",
        placeholder="종목명 또는 티커 입력 (예: 삼성전자, 005930)",
        key="trade_note_search",
    )

    _sel_ticker = _sel_name = _sel_close = _sel_sector = None

    if _sq_note and market_data_raw:
        _candidates = [
            (t, i.get("name", t), i.get("sector", "-"), i.get("close", 0))
            for t, i in market_data_raw.items()
            if _sq_note.lower() in i.get("name", "").lower() or _sq_note in t
        ]
        if _candidates:
            _opts = [f"{name}  ({ticker})  —  {sector}  |  현재가 {close:,}원"
                     for ticker, name, sector, close in _candidates[:30]]
            _idx = st.selectbox(
                f"검색 결과 {len(_candidates[:30])}건",
                range(len(_opts)),
                format_func=lambda i: _opts[i],
                key="trade_note_select",
            )
            _sel_ticker, _sel_name, _sel_sector, _sel_close = _candidates[_idx]
        else:
            st.info("일치하는 종목이 없습니다.")
    elif not _sq_note:
        st.caption("종목명 또는 티커를 입력하면 자동으로 검색됩니다.")

    # ② 종목 선택 후 매수 정보 입력 폼
    if _sel_ticker:
        st.success(f"선택: **{_sel_name}** `{_sel_ticker}` | {_sel_sector}")
        with st.form("add_trade_form", clear_on_submit=True):
            _f3, _f4, _f5 = st.columns(3)
            _new_price = _f3.number_input("매수가 (원)", min_value=1,
                                          value=int(_sel_close) if _sel_close else 10000,
                                          step=100)
            _new_qty   = _f4.number_input("수량 (주)", min_value=1, value=1, step=1)
            _new_date  = _f5.date_input("매수일", value=date.today())
            _new_note  = st.text_area("메모 (선택)",
                                      placeholder="매수 이유, 목표가, 전략 등",
                                      height=80)
            if st.form_submit_button("저장 📌", use_container_width=True):
                if _sel_name in _notes and _notes[_sel_name].get("status") == "보유중":
                    st.warning(f"'{_sel_name}'은 이미 보유 중입니다.")
                else:
                    _notes[_sel_name] = {
                        "ticker":     _sel_ticker,
                        "buy_price":  int(_new_price),
                        "quantity":   int(_new_qty),
                        "buy_date":   _new_date.isoformat(),
                        "note":       _new_note.strip(),
                        "peak_price": int(_new_price),
                        "status":     "보유중",
                    }
                    save_notes(_notes, TRADE_NOTES_BASE)
                    st.success(f"✅ '{_sel_name}' 등록 완료!")
                    st.rerun()

# ── 이력 탭 ──────────────────────────────────────────────────────────────────
with _tab_hist:
    _done = {k: v for k, v in _notes.items() if v.get("status") == "매도완료"}
    if not _done:
        st.info("매도 완료된 이력이 없습니다.")
    else:
        _hist_rows = []
        for _nm, _note in sorted(_done.items(),
                                 key=lambda x: x[1].get("sell_date", ""), reverse=True):
            _buy  = _note.get("buy_price", 0)
            _sell = _note.get("sell_price", _buy)
            _qty  = _note.get("quantity", 0)
            _real_pnl     = (_sell - _buy) * _qty
            _real_pnl_pct = (_sell - _buy) / _buy * 100 if _buy else 0
            _hist_rows.append({
                "종목명":   _nm,
                "매수가":   f"{_buy:,}",
                "매도가":   f"{_sell:,}",
                "수량":     _qty,
                "실현손익(원)": f"{_real_pnl:+,}",
                "실현손익(%)":  f"{_real_pnl_pct:+.2f}%",
                "매수일":   _note.get("buy_date", "-"),
                "매도일":   _note.get("sell_date", "-"),
            })
        st.dataframe(pd.DataFrame(_hist_rows), use_container_width=True, hide_index=True)
        _total_real = sum(
            (v.get("sell_price", 0) - v.get("buy_price", 0)) * v.get("quantity", 0)
            for v in _done.values()
        )
        st.metric("총 실현손익", f"{_total_real:+,}원",
                  delta_color="normal" if _total_real >= 0 else "inverse")

st.divider()

# ─── SECTION 4: 공시 경고 ────────────────────────────────────────────────────

st.markdown('<a id="disclosure"></a>', unsafe_allow_html=True)
st.header("⚠️ 섹터 Top10 공시 경고")
if rankings_data and ratios_data:
    warned = []
    for sector_v in rankings_data.values():
        for item in sector_v.get("top10", []):
            r = ratios_data.get(item["ticker"], {})
            if r.get("disclosure_warning"):
                warned.append(f"{item['ticker']} {item['name']}")
    if warned:
        st.warning("DART 데이터 누락 종목:\n\n" + "\n".join(f"- {w}" for w in warned))
    else:
        st.success("섹터 Top10 전원 DART 데이터 정상")

st.divider()

# ─── SECTION: AI 전력 인프라 관심종목 ────────────────────────────────────────

st.markdown('<a id="ai-power"></a>', unsafe_allow_html=True)
st.header("⚡ AI 전력 인프라 관심종목")

_AI_POWER_PATH = BASE_DIR / "data" / "ai_power_sector.json"

def _load_ai_power() -> dict:
    if not _AI_POWER_PATH.exists():
        return {}
    try:
        return json.loads(_AI_POWER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_ai_power(data: dict) -> None:
    _AI_POWER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

_ap = _load_ai_power()
if not _ap:
    st.warning("data/ai_power_sector.json 파일이 없습니다.")
else:
    st.caption(
        f"**{_ap.get('description', '')}**  \n"
        f"최종 수정: {_ap.get('last_updated', '—')} | 총 {sum(len(s['stocks']) for s in _ap.get('stages', []))}개 종목"
    )

    _SIZE_COLOR = {"대형": "#1e40af", "중형": "#065f46", "소형": "#92400e"}

    _stage_tabs = st.tabs([
        f"{s['emoji']} {s['stage']}단계: {s['name']}"
        for s in _ap.get("stages", [])
    ])

    for _tab, _stage in zip(_stage_tabs, _ap.get("stages", [])):
        with _tab:
            st.caption(f"**{_stage['description']}**")
            st.markdown("")

            _cols = st.columns(2)
            for _si, _stk in enumerate(_stage["stocks"]):
                _tk = _stk["ticker"]
                _mkt = (market_data_raw or {}).get(_tk, {})
                _close  = _mkt.get("close")
                _chg    = _mkt.get("change_rate")
                _mktcap = _mkt.get("market_cap")

                _close_str  = f"₩{_close:,.0f}" if _close else "—"
                _chg_str    = f"{_chg:+.2f}%" if _chg is not None else "—"
                _chg_color  = "#15803d" if (_chg or 0) >= 0 else "#b91c1c"
                _cap_str    = f"{_mktcap/100000000:,.0f}억" if _mktcap else "—"
                _sz_color   = _SIZE_COLOR.get(_stk.get("size", ""), "#374151")

                with _cols[_si % 2]:
                    st.markdown(
                        f"""<div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin-bottom:10px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
  <span style="font-weight:700;font-size:15px">{_stk['name']}</span>
  <span style="background:{_sz_color};color:#fff;font-size:11px;padding:2px 7px;border-radius:10px">{_stk.get('size','')}</span>
</div>
<div style="color:#6b7280;font-size:12px;margin-bottom:6px">{_tk}</div>
<div style="display:flex;gap:16px;font-size:13px;margin-bottom:8px">
  <span>현재가 <b>{_close_str}</b></span>
  <span style="color:{_chg_color}"><b>{_chg_str}</b></span>
  <span style="color:#6b7280">시총 {_cap_str}</span>
</div>
<div style="font-size:12px;color:#374151;line-height:1.5">{_stk['thesis']}</div>
</div>""",
                        unsafe_allow_html=True,
                    )

            # ── 재무·가격 테이블 ──────────────────────────────────────────────
            _stage_tickers = [s["ticker"] for s in _stage["stocks"]]
            if market_data_raw:
                st.markdown("##### 📊 재무·가격 데이터")
                with st.spinner("데이터 조회 중..."):
                    _stage_rows = _build_stock_rows(_stage_tickers)
                _render_stock_table(_stage_rows, height=len(_stage_tickers) * 38 + 60, table_key=f"ai_{_stage['key']}")
            else:
                st.caption("⚠️ 재무·가격 데이터 없음 — 파이프라인 실행 후 표시됩니다.")

            # ── 종목 추가 / 삭제 편집기 ──────────────────────────────────────
            with st.expander(f"✏️ {_stage['name']} 단계 종목 편집"):
                st.markdown("**종목 삭제**")
                _del_names = [s["name"] for s in _stage["stocks"]]
                _to_del = st.multiselect(
                    "삭제할 종목 선택",
                    _del_names,
                    key=f"ai_del_{_stage['key']}",
                )

                st.markdown("**종목 추가**")
                _c1, _c2, _c3 = st.columns([2, 2, 1])
                _new_name   = _c1.text_input("종목명", key=f"ai_add_name_{_stage['key']}", placeholder="예: 현대건설")
                _new_ticker = _c2.text_input("티커 (6자리)", key=f"ai_add_ticker_{_stage['key']}", placeholder="000720")
                _new_size   = _c3.selectbox("구분", ["대형", "중형", "소형"], key=f"ai_add_size_{_stage['key']}")
                _new_thesis = st.text_area("투자 논리 (한 줄)", key=f"ai_add_thesis_{_stage['key']}", placeholder="핵심 수혜 이유를 간결하게 기술")

                if st.button("💾 저장", key=f"ai_save_{_stage['key']}"):
                    _fresh = _load_ai_power()
                    for _fs in _fresh["stages"]:
                        if _fs["key"] == _stage["key"]:
                            _fs["stocks"] = [s for s in _fs["stocks"] if s["name"] not in _to_del]
                            if _new_name.strip() and _new_ticker.strip():
                                _fs["stocks"].append({
                                    "name": _new_name.strip(),
                                    "ticker": _new_ticker.strip().zfill(6),
                                    "size": _new_size,
                                    "thesis": _new_thesis.strip(),
                                })
                            break
                    _fresh["last_updated"] = date.today().isoformat()
                    _save_ai_power(_fresh)
                    st.success("저장 완료")
                    st.rerun()

st.divider()

# ─── SECTION 5: 파이프라인 로그 ──────────────────────────────────────────────

st.markdown('<a id="pipeline"></a>', unsafe_allow_html=True)
with st.expander("🔧 파이프라인 로그"):
    for log_path, label in [
        (OUTPUT_DIR / "pipeline_warn.log",  "경고"),
        (OUTPUT_DIR / "pipeline_error.log", "오류"),
    ]:
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")[-3000:]
            if content.strip():
                st.markdown(f"**{label} 로그**")
                st.code(content, language="text")
