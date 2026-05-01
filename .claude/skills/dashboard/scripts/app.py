"""
STEP 7 - Streamlit 대시보드 (LLM-Free)
섹터별 시총 Top10 추적 + 6개월 주목 섹터 + 재무비율 + 뉴스 아카이브
"""
import json
import os
from datetime import date, datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from pathlib import Path

import sys

# Streamlit Cloud: CWD = 레포 루트이므로 스크립트 디렉토리를 명시적으로 추가
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
WATCHLIST_PATH     = BASE_DIR / "data" / "watchlist.json"


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


def _load_watchlist() -> list[dict]:
    if "_wl_cache" not in st.session_state:
        try:
            st.session_state["_wl_cache"] = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8")) if WATCHLIST_PATH.exists() else []
        except Exception:
            st.session_state["_wl_cache"] = []
    return st.session_state["_wl_cache"]


def _save_watchlist(items: list[dict]) -> None:
    st.session_state["_wl_cache"] = items
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _wl_add(ticker: str, name: str, note: str = "", target_price: int = 0, group: str = "") -> bool:
    """관심 종목 추가. 이미 있으면 False 반환."""
    wl = _load_watchlist()
    if any(w["ticker"] == ticker for w in wl):
        return False
    wl.append({
        "ticker": ticker, "name": name,
        "added_date": date.today().isoformat(),
        "note": note, "target_price": target_price, "group": group,
    })
    _save_watchlist(wl)
    return True


def _wl_remove(ticker: str) -> None:
    wl = [w for w in _load_watchlist() if w["ticker"] != ticker]
    _save_watchlist(wl)


def _wl_button(ticker: str, name: str, key_suffix: str = "") -> None:
    """테이블/카드 옆에 붙이는 ⭐ 버튼 (이미 추가됐으면 비활성)."""
    wl = _load_watchlist()
    already = any(w["ticker"] == ticker for w in wl)
    label = "⭐ 추가됨" if already else "☆ 관심 추가"
    if st.button(label, key=f"wl_btn_{ticker}_{key_suffix}", disabled=already,
                 use_container_width=True):
        _wl_add(ticker, name)
        st.rerun()


# ─── 미국 주식 (yfinance) 헬퍼 ────────────────────────────────────────────────

US_WATCHLIST_PATH = BASE_DIR / "data" / "us_watchlist.json"

def _load_us_watchlist() -> list[dict]:
    if "_us_wl_cache" not in st.session_state:
        try:
            st.session_state["_us_wl_cache"] = (
                json.loads(US_WATCHLIST_PATH.read_text(encoding="utf-8"))
                if US_WATCHLIST_PATH.exists() else []
            )
        except Exception:
            st.session_state["_us_wl_cache"] = []
    return st.session_state["_us_wl_cache"]


def _save_us_watchlist(items: list[dict]) -> None:
    st.session_state["_us_wl_cache"] = items
    US_WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    US_WATCHLIST_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _us_wl_add(ticker: str, name: str, note: str = "", target_price: float = 0.0) -> bool:
    wl = _load_us_watchlist()
    if any(w["ticker"] == ticker for w in wl):
        return False
    wl.append({"ticker": ticker, "name": name, "added_date": date.today().isoformat(),
                "note": note, "target_price": target_price})
    _save_us_watchlist(wl)
    return True


def _us_wl_remove(ticker: str) -> None:
    _save_us_watchlist([w for w in _load_us_watchlist() if w["ticker"] != ticker])


try:
    import yfinance as _yf
    _YF_OK = True
except ImportError:
    _yf = None  # type: ignore
    _YF_OK = False


@st.cache_data(show_spinner=False, ttl=180)
def _yf_price(ticker: str) -> dict:
    """yfinance fast_info → price dict. 3분 캐시."""
    if not _YF_OK:
        return {}
    try:
        fi = _yf.Ticker(ticker).fast_info
        last  = getattr(fi, "last_price",     None)
        prev  = getattr(fi, "previous_close", None)
        chg   = (last / prev - 1) * 100 if last and prev and prev != 0 else None
        return {
            "last":     last,
            "prev":     prev,
            "chg":      chg,
            "high52":   getattr(fi, "year_high",   None),
            "low52":    getattr(fi, "year_low",    None),
            "mktcap":   getattr(fi, "market_cap",  None),
            "currency": getattr(fi, "currency",    "USD"),
        }
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=180)
def _yf_history(ticker: str, period: str = "1y") -> "pd.DataFrame | None":
    """yfinance OHLCV 히스토리. 3분 캐시."""
    if not _YF_OK:
        return None
    try:
        df = _yf.Ticker(ticker).history(period=period)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        # 스파이크 제거
        if all(c in df.columns for c in ["Open", "High", "Low", "Close"]):
            _rm = df["Close"].rolling(15, min_periods=1, center=True).median()
            _mask = (df["High"] <= _rm * 3.0) & (df["Low"] >= _rm * 0.2) & (df["Close"] >= _rm * 0.2)
            df = df[_mask].reset_index(drop=True)
        return df
    except Exception:
        return None


_YF_PERIOD_MAP = {"1개월": "1mo", "3개월": "3mo", "6개월": "6mo", "1년": "1y", "2년": "2y"}


def _render_us_chart(ticker: str, name: str, key_prefix: str = "") -> None:
    """미국 주식 캔들차트 (yfinance). 국내 차트와 동일 컨셉."""
    if not _YF_OK:
        st.warning("yfinance 미설치 — `pip install yfinance`")
        return
    _uc1, _uc2 = st.columns([3, 4])
    with _uc1:
        _u_period = st.radio("기간", list(_YF_PERIOD_MAP.keys()),
                             index=3, horizontal=True, key=f"ucp_{key_prefix}_{ticker}")
    with _uc2:
        _u_ma = st.multiselect("이동평균선", ["5일", "20일", "60일"],
                               default=["20일", "60일"], key=f"uma_{key_prefix}_{ticker}")
    _u_hist = _yf_history(ticker, _YF_PERIOD_MAP[_u_period])
    if _u_hist is None or _u_hist.empty:
        st.warning(f"{name}({ticker}) 데이터를 가져올 수 없습니다.")
        return
    _has_ohlc = all(c in _u_hist.columns for c in ["Open", "High", "Low", "Close"])
    _has_vol  = "Volume" in _u_hist.columns
    _uf = make_subplots(rows=2 if _has_vol else 1, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25] if _has_vol else [1.0], vertical_spacing=0.03)
    if _has_ohlc:
        _uf.add_trace(go.Candlestick(
            x=_u_hist["Date"], open=_u_hist["Open"], high=_u_hist["High"],
            low=_u_hist["Low"], close=_u_hist["Close"], name=name,
            increasing_line_color="#15803d", decreasing_line_color="#b91c1c",
            increasing_fillcolor="#15803d", decreasing_fillcolor="#b91c1c",
        ), row=1, col=1)
    else:
        _uf.add_trace(go.Scatter(x=_u_hist["Date"], y=_u_hist["Close"], mode="lines",
                                  name=name, line=dict(color="#1e40af", width=2)), row=1, col=1)
    _u_ma_map = {"5일": (5, "#f59e0b"), "20일": (20, "#3b82f6"), "60일": (60, "#8b5cf6")}
    for _um in _u_ma:
        _ud, _uc = _u_ma_map[_um]
        if len(_u_hist) >= _ud:
            _uf.add_trace(go.Scatter(x=_u_hist["Date"],
                                      y=_u_hist["Close"].rolling(_ud).mean(),
                                      mode="lines", name=f"MA{_ud}",
                                      line=dict(color=_uc, width=1.5)), row=1, col=1)
    if _has_vol:
        _uvc = ["#15803d" if (_has_ohlc and _u_hist["Close"].iloc[i] >= _u_hist["Open"].iloc[i])
                else "#b91c1c" for i in range(len(_u_hist))]
        _uf.add_trace(go.Bar(x=_u_hist["Date"], y=_u_hist["Volume"],
                              marker_color=_uvc, showlegend=False), row=2, col=1)
        _uf.update_yaxes(title_text="거래량", tickformat=".2s", row=2, col=1)
    _uf.update_layout(title=f"{name} ({ticker})", xaxis_rangeslider_visible=False,
                       height=520, margin=dict(l=10, r=10, t=45, b=10),
                       paper_bgcolor="white", plot_bgcolor="#fafafa",
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=11)))
    _uf.update_xaxes(showgrid=True, gridcolor="#e5e7eb")
    _uf.update_yaxes(showgrid=True, gridcolor="#e5e7eb", row=1, col=1)
    st.plotly_chart(_uf, use_container_width=True)


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
        return datetime.fromtimestamp(path.stat().st_mtime, tz=KST).strftime("%m/%d %H:%M KST")
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
/* 탭 색상 구분 */
button[data-baseweb="tab"]:nth-of-type(1) { border-bottom: 3px solid #f59e0b !important; }
button[data-baseweb="tab"]:nth-of-type(2) { border-bottom: 3px solid #3b82f6 !important; }
button[data-baseweb="tab"]:nth-of-type(3) { border-bottom: 3px solid #22c55e !important; }
button[data-baseweb="tab"]:nth-of-type(4) { border-bottom: 3px solid #8b5cf6 !important; }
button[data-baseweb="tab"]:nth-of-type(5) { border-bottom: 3px solid #ef4444 !important; }
button[data-baseweb="tab"]:nth-of-type(6) { border-bottom: 3px solid #0ea5e9 !important; }
button[data-baseweb="tab"]:nth-of-type(7) { border-bottom: 3px solid #6b7280 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(1) { background:#fef3c7 !important; color:#92400e !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(2) { background:#dbeafe !important; color:#1e40af !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(3) { background:#dcfce7 !important; color:#166534 !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(4) { background:#f3e8ff !important; color:#6b21a8 !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(5) { background:#fee2e2 !important; color:#991b1b !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(6) { background:#e0f2fe !important; color:#0c4a6e !important; font-weight:700 !important; }
button[data-baseweb="tab"][aria-selected="true"]:nth-of-type(7) { background:#f1f5f9 !important; color:#374151 !important; font-weight:700 !important; }
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

    # 사이드바 관심 종목 미니 목록
    st.markdown("---")
    _sb_wl = _load_watchlist()
    if _sb_wl:
        st.markdown("### ⭐ 관심 종목")
        for _w in _sb_wl[:10]:
            _tk = _w["ticker"]
            _nm = _w["name"]
            _c1d = market_data_raw.get(_tk, {}).get("change_rate")
            _close_sb = market_data_raw.get(_tk, {}).get("close")
            _c1d_str = f"{_c1d:+.2f}%" if _c1d is not None else "—"
            _color = "#16a34a" if (_c1d or 0) >= 0 else "#dc2626"
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;font-size:13px;'
                f'padding:2px 0">'
                f'<span>{_nm}</span>'
                f'<span style="color:{_color};font-weight:600">{_c1d_str}</span></div>',
                unsafe_allow_html=True,
            )
        if len(_sb_wl) > 10:
            st.caption(f"+ {len(_sb_wl)-10}개 더")

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
_now_kst = datetime.now(KST)
st.caption(f"기준일: {date.today().strftime('%Y-%m-%d')} | 갱신: {_now_kst.strftime('%H:%M KST')}")

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

# ─── 글로벌 시장 지수 ─────────────────────────────────────────────────────────

_IDX_TICKERS = [
    ("^KS11",  "KOSPI",    "🇰🇷"),
    ("^KQ11",  "KOSDAQ",   "🇰🇷"),
    ("^GSPC",  "S&P 500",  "🇺🇸"),
    ("^IXIC",  "NASDAQ",   "🇺🇸"),
    ("^DJI",   "DOW",      "🇺🇸"),
]

@st.cache_data(show_spinner=False, ttl=300)
def _fetch_indices() -> list[dict]:
    if not _YF_OK:
        return []
    results = []
    for tkr, label, flag in _IDX_TICKERS:
        try:
            fi = _yf.Ticker(tkr).fast_info
            last = getattr(fi, "last_price",     None)
            prev = getattr(fi, "previous_close", None)
            chg  = (last / prev - 1) * 100 if last and prev and prev != 0 else None
            results.append({"label": label, "flag": flag, "last": last, "chg": chg})
        except Exception:
            results.append({"label": label, "flag": flag, "last": None, "chg": None})
    return results

_idx_data = _fetch_indices()
if _idx_data:
    _idx_cols = st.columns(len(_idx_data))
    for _ic, _id in enumerate(_idx_data):
        _ilast = _id["last"]
        _ichg  = _id["chg"]
        _icolor = "#15803d" if (_ichg or 0) >= 0 else "#b91c1c"
        _ichg_str = f"{_ichg:+.2f}%" if _ichg is not None else "—"
        _ilast_str = f"{_ilast:,.2f}" if _ilast else "—"
        with _idx_cols[_ic]:
            st.markdown(
                f"""<div style="background:{'#f0fdf4' if (_ichg or 0)>=0 else '#fef2f2'};
border:1px solid {'#86efac' if (_ichg or 0)>=0 else '#fca5a5'};border-radius:8px;
padding:8px 12px;text-align:center">
  <div style="font-size:12px;color:#6b7280">{_id['flag']} {_id['label']}</div>
  <div style="font-size:17px;font-weight:700;color:#111">{_ilast_str}</div>
  <div style="font-size:13px;font-weight:700;color:{_icolor}">{_ichg_str}</div>
</div>""",
                unsafe_allow_html=True,
            )

st.divider()


# ─── 공통 테이블·차트 헬퍼 (모든 섹션에서 공유) ───────────────────────────────────

def _qf_n(v, d=1):
    if v is None or (isinstance(v, float) and v != v): return "-"
    r = round(float(v), d)
    return str(int(r)) if r == int(r) else str(r)

def _chg_fmt_n(v) -> str:
    if v is None or (isinstance(v, float) and v != v): return "-"
    return f"{float(v):+.2f}%"

def _period_chg(c_near, c_far) -> float | None:
    """누적 등락률 두 개로 구간 등락률 계산."""
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

def _render_stock_chart(ticker: str, name: str, days: int = 252) -> None:
    """캔들스틱 + MA + 추세선 + 매매 신호. historical CSV → pykrx fallback."""
    from datetime import timedelta

    # ── 1. 최대 2년치 로드 ─────────────────────────────────────────────────
    _MAX_LOAD = 520
    hist_path = BASE_DIR / "data" / "historical" / f"{ticker}.csv"
    df_full = None
    if hist_path.exists():
        try:
            df_full = pd.read_csv(hist_path, parse_dates=["Date"])
            df_full = df_full.sort_values("Date").tail(_MAX_LOAD).reset_index(drop=True)
        except Exception:
            df_full = None
    if df_full is None or df_full.empty:
        try:
            from pykrx import stock as _ks
            _end = datetime.now()
            _start = _end - timedelta(days=_MAX_LOAD + 60)
            _raw = _ks.get_market_ohlcv_by_date(_start.strftime("%Y%m%d"), _end.strftime("%Y%m%d"), ticker)
            if _raw is not None and not _raw.empty:
                _raw = _raw.reset_index()
                _raw.columns = ["Date" if c == "날짜" else c for c in _raw.columns]
                _raw = _raw.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
                df_full = _raw[["Date"] + [c for c in ["Open","High","Low","Close","Volume"] if c in _raw.columns]]
        except Exception:
            df_full = None
    if df_full is None or df_full.empty:
        st.warning(f"{name}({ticker}) 차트 데이터를 불러올 수 없습니다.")
        return

    # ── 1b. 이상치(스파이크) 제거 ─────────────────────────────────────────────
    # pykrx/CSV에서 가끔 분할·병합 오류로 극단값이 섞임 → 롤링 중앙값 대비 3배 초과 행 제거
    if all(c in df_full.columns for c in ["Open", "High", "Low", "Close"]):
        _roll_med = df_full["Close"].rolling(15, min_periods=1, center=True).median()
        _spike_mask = (
            (df_full["High"]  <= _roll_med * 3.0) &
            (df_full["Low"]   >= _roll_med * 0.2) &
            (df_full["Open"]  >= _roll_med * 0.2) &
            (df_full["Close"] >= _roll_med * 0.2)
        )
        df_full = df_full[_spike_mask].reset_index(drop=True)

    # ── 2. 컨트롤 ────────────────────────────────────────────────────────────
    _c1, _c2, _c3 = st.columns([2, 4, 2])
    with _c1:
        _period = st.radio("기간", ["1개월", "3개월", "6개월", "1년", "2년"],
                           index=3, horizontal=True, key=f"cp_{ticker}")
    with _c2:
        _ma_sel = st.multiselect("이동평균선",
                                 ["5일", "20일", "60일", "120일"],
                                 default=["20일", "60일"],
                                 key=f"ma_{ticker}")
    with _c3:
        _show_trend = st.checkbox("저항선·지지선", value=True, key=f"tr_{ticker}")

    _n = {"1개월": 21, "3개월": 63, "6개월": 126, "1년": 252, "2년": 504}[_period]
    df = df_full.tail(_n).reset_index(drop=True)
    has_ohlc = all(c in df.columns for c in ["Open", "High", "Low", "Close"])

    # ── 3. Figure ─────────────────────────────────────────────────────────────
    has_vol = "Volume" in df.columns
    fig = make_subplots(
        rows=2 if has_vol else 1, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25] if has_vol else [1.0],
        vertical_spacing=0.03,
    )

    # ── 4. 캔들스틱 ──────────────────────────────────────────────────────────
    if has_ohlc:
        fig.add_trace(go.Candlestick(
            x=df["Date"], open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], name=name,
            increasing_line_color="#15803d", decreasing_line_color="#b91c1c",
            increasing_fillcolor="#15803d", decreasing_fillcolor="#b91c1c",
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(x=df["Date"], y=df["Close"], mode="lines", name=name,
                                 line=dict(color="#1e40af", width=2)), row=1, col=1)

    # ── 5. 이동평균선 ─────────────────────────────────────────────────────────
    _ma_color = {"5일": "#f59e0b", "20일": "#3b82f6", "60일": "#8b5cf6", "120일": "#ef4444"}
    _ma_days  = {"5일": 5, "20일": 20, "60일": 60, "120일": 120}
    for _m in _ma_sel:
        _p = _ma_days[_m]
        _ma_vals = df_full["Close"].rolling(_p).mean().tail(_n).values
        fig.add_trace(go.Scatter(
            x=df["Date"], y=_ma_vals, mode="lines", name=f"MA{_p}",
            line=dict(color=_ma_color[_m], width=1.5),
        ), row=1, col=1)

    # ── 6. 추세선 + 매매 신호 ────────────────────────────────────────────────
    _resist_coef = None
    _support_coef = None

    if _show_trend and has_ohlc and len(df) >= 30:
        _highs = df["High"].values.astype(float)
        _lows  = df["Low"].values.astype(float)
        _close = df["Close"].values.astype(float)
        _idx   = np.arange(len(df))
        _win   = max(7, len(df) // 25)

        _h_ser = pd.Series(_highs)
        _l_ser = pd.Series(_lows)
        _peak_mask   = (_h_ser == _h_ser.rolling(_win * 2 + 1, center=True).max())
        _trough_mask = (_l_ser == _l_ser.rolling(_win * 2 + 1, center=True).min())
        _peak_idx   = _idx[_peak_mask.values]
        _trough_idx = _idx[_trough_mask.values]

        # 저항선 계수 계산 (피벗 고점 상위 5개 회귀)
        if len(_peak_idx) >= 2:
            _top_peaks = sorted(sorted(_peak_idx, key=lambda i: _highs[i], reverse=True)[:5])
            _resist_coef = np.polyfit(np.array(_top_peaks), _highs[_top_peaks], 1)
            _rs = _resist_coef[0]
            _rl, _rc = ("하락저항선", "#ef4444") if _rs < 0 else ("상승저항선", "#f97316")
            fig.add_trace(go.Scatter(
                x=[df["Date"].iloc[0], df["Date"].iloc[-1]],
                y=[float(np.polyval(_resist_coef, 0)), float(np.polyval(_resist_coef, _idx[-1]))],
                mode="lines", name=_rl,
                line=dict(color=_rc, width=2, dash="dot"),
            ), row=1, col=1)

        # 지지선 계수 계산 (피벗 저점 하위 5개 회귀)
        if len(_trough_idx) >= 2:
            _bot_troughs = sorted(sorted(_trough_idx, key=lambda i: _lows[i])[:5])
            _support_coef = np.polyfit(np.array(_bot_troughs), _lows[_bot_troughs], 1)
            _ss = _support_coef[0]
            _sl, _sc = ("상승지지선", "#22c55e") if _ss > 0 else ("하락지지선", "#06b6d4")
            fig.add_trace(go.Scatter(
                x=[df["Date"].iloc[0], df["Date"].iloc[-1]],
                y=[float(np.polyval(_support_coef, 0)), float(np.polyval(_support_coef, _idx[-1]))],
                mode="lines", name=_sl,
                line=dict(color=_sc, width=2, dash="dot"),
            ), row=1, col=1)

        # ── 매매 신호 마커 ───────────────────────────────────────────────────
        # 지지선 터치(매수): 저가가 지지선 ±2% 이내에 닿고 종가가 지지선 위
        # 저항선 터치(매도): 고가가 저항선 ±2% 이내에 닿고 종가가 저항선 아래
        # 저항선 돌파(강한 매수): 전일 종가 < 저항선, 금일 종가 > 저항선
        # 지지선 이탈(강한 매도): 전일 종가 > 지지선, 금일 종가 < 지지선
        _NEAR = 0.022
        _buy_x, _buy_y, _buy_hover   = [], [], []
        _sell_x, _sell_y, _sell_hover = [], [], []
        _bo_x, _bo_y, _bo_hover       = [], [], []   # breakout
        _bd_x, _bd_y, _bd_hover       = [], [], []   # breakdown

        for _i in range(1, len(df)):
            _sv = float(np.polyval(_support_coef, _i)) if _support_coef is not None else None
            _rv = float(np.polyval(_resist_coef,  _i)) if _resist_coef  is not None else None
            _sv_p = float(np.polyval(_support_coef, _i-1)) if _support_coef is not None else None
            _rv_p = float(np.polyval(_resist_coef,  _i-1)) if _resist_coef  is not None else None
            _c = _close[_i]; _pc = _close[_i-1]
            _lo = _lows[_i]; _hi = _highs[_i]
            _dt = df["Date"].iloc[_i]

            if _sv is not None:
                if _pc >= _sv_p and _c < _sv:                          # 지지 이탈
                    _bd_x.append(_dt); _bd_y.append(_lo * 0.975)
                    _bd_hover.append(f"지지 이탈 ₩{int(_c):,} ({_dt})")
                elif abs(_lo - _sv) / _sv <= _NEAR and _c >= _sv:     # 지지선 터치
                    _buy_x.append(_dt); _buy_y.append(_lo * 0.985)
                    _buy_hover.append(f"지지선 터치 ₩{int(_c):,} ({_dt})")

            if _rv is not None:
                if _pc <= _rv_p and _c > _rv:                          # 저항 돌파
                    _bo_x.append(_dt); _bo_y.append(_hi * 1.025)
                    _bo_hover.append(f"저항 돌파 ₩{int(_c):,} ({_dt})")
                elif abs(_hi - _rv) / _rv <= _NEAR and _c <= _rv:     # 저항선 터치
                    _sell_x.append(_dt); _sell_y.append(_hi * 1.015)
                    _sell_hover.append(f"저항선 터치 ₩{int(_c):,} ({_dt})")

        if _buy_x:
            fig.add_trace(go.Scatter(
                x=_buy_x, y=_buy_y, mode="markers", name="매수 구간",
                marker=dict(symbol="triangle-up", size=11, color="#16a34a",
                            line=dict(color="#14532d", width=1)),
                hovertext=_buy_hover, hoverinfo="text",
            ), row=1, col=1)
        if _sell_x:
            fig.add_trace(go.Scatter(
                x=_sell_x, y=_sell_y, mode="markers", name="매도 구간",
                marker=dict(symbol="triangle-down", size=11, color="#dc2626",
                            line=dict(color="#7f1d1d", width=1)),
                hovertext=_sell_hover, hoverinfo="text",
            ), row=1, col=1)
        if _bo_x:
            fig.add_trace(go.Scatter(
                x=_bo_x, y=_bo_y, mode="markers", name="저항 돌파 ★",
                marker=dict(symbol="star", size=14, color="#d97706",
                            line=dict(color="#78350f", width=1)),
                hovertext=_bo_hover, hoverinfo="text",
            ), row=1, col=1)
        if _bd_x:
            fig.add_trace(go.Scatter(
                x=_bd_x, y=_bd_y, mode="markers", name="지지 이탈 ✕",
                marker=dict(symbol="x", size=13, color="#7f1d1d",
                            line=dict(color="#450a0a", width=2)),
                hovertext=_bd_hover, hoverinfo="text",
            ), row=1, col=1)

    # ── 7. 거래량 ─────────────────────────────────────────────────────────────
    if has_vol:
        _vol_c = [
            "#15803d" if (has_ohlc and df["Close"].iloc[i] >= df["Open"].iloc[i]) else "#b91c1c"
            for i in range(len(df))
        ]
        fig.add_trace(go.Bar(
            x=df["Date"], y=df["Volume"], name="거래량",
            marker_color=_vol_c, showlegend=False,
        ), row=2, col=1)
        fig.update_yaxes(title_text="거래량", tickformat=".2s", row=2, col=1)

    # ── 8. 레이아웃 ───────────────────────────────────────────────────────────
    fig.update_layout(
        title=f"{name} ({ticker})",
        xaxis_rangeslider_visible=False,
        height=590,
        margin=dict(l=10, r=10, t=45, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=11)),
        paper_bgcolor="white", plot_bgcolor="#fafafa",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb", gridwidth=1)
    fig.update_yaxes(showgrid=True, gridcolor="#e5e7eb", gridwidth=1, row=1, col=1)
    st.plotly_chart(fig, use_container_width=True)

    # ── 9. 현재 신호 요약 카드 ────────────────────────────────────────────────
    if _show_trend and has_ohlc and (_resist_coef is not None or _support_coef is not None):
        _last_i = len(df) - 1
        _last_c = float(df["Close"].iloc[_last_i])
        _last_d = str(df["Date"].iloc[_last_i])[:10]
        _sv_now = float(np.polyval(_support_coef, _last_i)) if _support_coef is not None else None
        _rv_now = float(np.polyval(_resist_coef,  _last_i)) if _resist_coef  is not None else None

        # 신호 판정
        if _sv_now and _rv_now:
            _ds = (_last_c - _sv_now) / _sv_now * 100   # 지지선까지 거리 (양수=위)
            _dr = (_rv_now - _last_c) / _last_c * 100   # 저항선까지 거리 (양수=아래)
            if _last_c < _sv_now:
                _sig = "🔴 지지선 이탈"
                _desc = f"종가가 지지선을 {abs(_ds):.1f}% 하회. 추세 약화 — 추가 하락 경계, 보유 비중 축소 고려."
                _act  = "매도 / 관망"
                _bg, _bd = "#fef2f2", "#ef4444"
            elif _ds <= 2.5:
                _sig = "🟢 매수 구간 (지지선 근접)"
                _desc = f"현재가가 지지선보다 {_ds:.1f}% 위 — 지지선 터치 구간. 반등 가능성 높음."
                _act  = "분할 매수 검토"
                _bg, _bd = "#f0fdf4", "#16a34a"
            elif _last_c > _rv_now:
                _sig = "🟡 저항선 돌파"
                _desc = f"종가가 저항선을 {abs(_dr):.1f}% 상회. 추세 전환 신호 — 거래량 동반 시 추가 상승 기대."
                _act  = "추가 매수 검토 (거래량 확인)"
                _bg, _bd = "#fffbeb", "#d97706"
            elif _dr <= 2.5:
                _sig = "🔴 매도 구간 (저항선 근접)"
                _desc = f"현재가가 저항선보다 {_dr:.1f}% 아래 — 저항선 터치 구간. 돌파 실패 시 조정 주의."
                _act  = "일부 차익 실현 검토"
                _bg, _bd = "#fef2f2", "#dc2626"
            else:
                _sig = "⚪ 중립 구간"
                _desc = f"지지선 +{_ds:.1f}% / 저항선 -{_dr:.1f}% — 뚜렷한 매매 신호 없음."
                _act  = "관망"
                _bg, _bd = "#f9fafb", "#9ca3af"
        elif _sv_now:
            _ds = (_last_c - _sv_now) / _sv_now * 100
            _sig = "🟢 지지선 근접" if _ds <= 2.5 else ("🔴 지지 이탈" if _last_c < _sv_now else "⚪ 중립")
            _desc = f"지지선 대비 {_ds:+.1f}%"
            _act  = "분할 매수" if _ds <= 2.5 else ("매도" if _last_c < _sv_now else "관망")
            _bg, _bd = "#f0fdf4" if _ds <= 2.5 else "#f9fafb", "#16a34a" if _ds <= 2.5 else "#9ca3af"
        else:
            _dr = (_rv_now - _last_c) / _last_c * 100 if _rv_now else None
            _sig = "🔴 저항선 근접" if (_dr is not None and _dr <= 2.5) else "⚪ 중립"
            _desc = f"저항선 대비 {_dr:+.1f}%" if _dr is not None else "추세선 데이터 부족"
            _act  = "일부 매도" if (_dr is not None and _dr <= 2.5) else "관망"
            _bg, _bd = "#fef2f2" if (_dr is not None and _dr <= 2.5) else "#f9fafb", "#dc2626" if (_dr is not None and _dr <= 2.5) else "#9ca3af"

        st.markdown(f"""
<div style="background:{_bg};border-radius:8px;padding:12px 16px;
            border-left:5px solid {_bd};margin-top:4px">
  <div style="font-size:13px;color:#6b7280;margin-bottom:4px">
    현재 신호 &nbsp;·&nbsp; {_last_d} 종가 <b>₩{int(_last_c):,}</b>
  </div>
  <div style="font-size:15px;font-weight:700;margin-bottom:4px">{_sig}</div>
  <div style="font-size:13px;color:#374151;margin-bottom:6px">{_desc}</div>
  <div style="font-size:12px;background:rgba(0,0,0,0.05);
              display:inline-block;padding:2px 10px;border-radius:4px">
    권고 액션: <b>{_act}</b>
  </div>
  <div style="font-size:11px;color:#9ca3af;margin-top:8px">
    ※ 추세선은 과거 피벗 고점·저점의 회귀선입니다. 투자 판단은 본인 책임이며 참고 용도로만 활용하세요.
  </div>
</div>
""", unsafe_allow_html=True)

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
    _p_1d    = list(_rdf["chg_1d"])
    _p_1_7   = [_period_chg(r["chg_1d"],  r["chg_7d"])  for r in rows]
    _p_7_15  = [_period_chg(r["chg_7d"],  r["chg_15d"]) for r in rows]
    _p_15_30 = [_period_chg(r["chg_15d"], r["chg_30d"]) for r in rows]
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


# ─── 메인 탭 ─────────────────────────────────────────────────────────────────

_tab_watch, _tab_market, _tab_screen, _tab_news, _tab_trade, _tab_system = st.tabs([
    "⭐ 관심종목·미래산업",
    "🌟 시장분석",
    "🔬 종목탐색",
    "📰 뉴스·공시",
    "📒 트레이드",
    "⚙️ 시스템",
])

with _tab_watch:
    # ─── SECTION 0: 관심 종목 ─────────────────��───────────────────────────────────

    st.markdown('<a id="watchlist"></a>', unsafe_allow_html=True)
    st.header("⭐ 관심 종목")

    _wl_items = _load_watchlist()

    # 상단 컨트롤 행
    _wc1, _wc2, _wc3 = st.columns([3, 2, 2])
    with _wc3:
        with st.popover("➕ 종목 추가", use_container_width=True):
            _add_q = st.text_input("종목명 또는 티커", key="wl_add_query",
                                   placeholder="삼성전자, 005930")
            _add_matches = []
            if _add_q and market_data_raw:
                _add_matches = [
                    (tkr, info) for tkr, info in market_data_raw.items()
                    if _add_q.lower() in info.get("name","").lower() or _add_q in tkr
                ][:10]
            if _add_matches:
                _add_sel = st.selectbox("종목 선택",
                                        [f"{info.get('name',tkr)} ({tkr})" for tkr, info in _add_matches],
                                        key="wl_add_sel")
                _add_idx = next((i for i, (tkr, info) in enumerate(_add_matches)
                                 if f"{info.get('name',tkr)} ({tkr})" == _add_sel), 0)
                _add_tkr, _add_info = _add_matches[_add_idx]
                _add_name = _add_info.get("name", _add_tkr)
                _add_close = int(_add_info.get("close") or 0)
                _awf1, _awf2 = st.columns(2)
                _wl_target = _awf1.number_input("목표가(원)", value=_add_close, min_value=0, step=100, key="wl_target")
                _wl_group  = _awf2.text_input("그룹", placeholder="반도체·AI 등", key="wl_group")
                _wl_note   = st.text_input("메모", placeholder="관심 이유...", key="wl_note")
                if st.button("⭐ 관심 종목 추가", use_container_width=True, key="wl_add_confirm"):
                    _ok = _wl_add(_add_tkr, _add_name, note=_wl_note,
                                   target_price=int(_wl_target), group=_wl_group)
                    if _ok:
                        st.success(f"{_add_name} 추가됨!")
                        st.rerun()
                    else:
                        st.warning("이미 추가된 종목입니다.")

    with _wc2:
        _wl_sort = st.selectbox("정렬", ["추가일↓", "전일등락↓", "목표괴리↓", "그룹"],
                                 key="wl_sort", label_visibility="collapsed")

    if not _wl_items:
        st.info("관심 종목이 없습니다. ➕ 종목 추가 버튼으로 추가하세요.")
    else:
        # 현재가 + 등락률 데이터 병합
        _wl_tickers = [w["ticker"] for w in _wl_items]
        _wl_mkt = {tkr: market_data_raw.get(tkr, {}) for tkr in _wl_tickers}

        def _wl_row(w: dict) -> dict:
            tkr = w["ticker"]
            m   = _wl_mkt.get(tkr, {})
            close = m.get("close") or 0
            c1d   = m.get("change_rate")
            target = w.get("target_price") or 0
            gap    = round((target / close - 1) * 100, 2) if close and target else None
            return {
                "_ticker": tkr, "_name": w["name"],
                "_close": close, "_chg1d": c1d,
                "_target": target, "_gap": gap,
                "_group": w.get("group",""),
                "_note": w.get("note",""),
                "_added": w.get("added_date",""),
            }

        _wl_rows = [_wl_row(w) for w in _wl_items]

        # 정렬
        if _wl_sort == "전일등락↓":
            _wl_rows.sort(key=lambda r: (r["_chg1d"] or -999), reverse=True)
        elif _wl_sort == "목표괴리↓":
            _wl_rows.sort(key=lambda r: (r["_gap"] or -999), reverse=True)
        elif _wl_sort == "그룹":
            _wl_rows.sort(key=lambda r: r["_group"])
        else:
            _wl_rows.sort(key=lambda r: r["_added"], reverse=True)

        # 그룹 필터
        _wl_groups = sorted({r["_group"] for r in _wl_rows if r["_group"]})
        with _wc1:
            if _wl_groups:
                _sel_grp = st.multiselect("그룹", _wl_groups, default=[],
                                           key="wl_grp_filter", label_visibility="collapsed",
                                           placeholder="전체 그룹")
                if _sel_grp:
                    _wl_rows = [r for r in _wl_rows if r["_group"] in _sel_grp]

        # 테이블 렌더
        _wl_disp = pd.DataFrame({
            "종목명":   [r["_name"]    for r in _wl_rows],
            "티커":     [r["_ticker"]  for r in _wl_rows],
            "그룹":     [r["_group"]   for r in _wl_rows],
            "현재가":   [f"₩{int(r['_close']):,}" if r["_close"] else "—" for r in _wl_rows],
            "전일(%)":  [round(r["_chg1d"], 2) if r["_chg1d"] is not None else None for r in _wl_rows],
            "목표가":   [f"₩{int(r['_target']):,}" if r["_target"] else "—" for r in _wl_rows],
            "목표괴리(%)": [r["_gap"] for r in _wl_rows],
            "메모":     [r["_note"]    for r in _wl_rows],
            "추가일":   [r["_added"]   for r in _wl_rows],
        })
        _wl_col_names = list(_wl_disp.columns)

        def _wl_style(row):
            styles = [""] * len(_wl_col_names)
            _apply_chg_style(styles, _wl_col_names, "전일(%)", row["전일(%)"])
            _apply_chg_style(styles, _wl_col_names, "목표괴리(%)", row["목표괴리(%)"])
            return styles

        _wl_styled = _wl_disp.style.apply(_wl_style, axis=1)
        _wl_ev = st.dataframe(_wl_styled, use_container_width=True,
                               height=min(600, 80 + len(_wl_rows) * 38),
                               on_select="rerun", selection_mode="single-row",
                               key="wl_tbl", hide_index=True)
        _wl_sel = (_wl_ev.selection.rows or []) if hasattr(_wl_ev, "selection") else []

        # 선택 시 차트 + 편집/삭제
        if _wl_sel and _wl_sel[0] < len(_wl_rows):
            _ws = _wl_rows[_wl_sel[0]]
            _we_item = next((w for w in _wl_items if w["ticker"] == _ws["_ticker"]), None)
            _w_exp_col1, _w_exp_col2 = st.columns([3, 1])
            with _w_exp_col1:
                with st.expander(f"📈 {_ws['_name']} ({_ws['_ticker']}) 차트", expanded=True):
                    _render_stock_chart(_ws["_ticker"], _ws["_name"])
            with _w_exp_col2:
                with st.expander("✏️ 편집 / 삭제", expanded=True):
                    if _we_item:
                        _cur_price = int(_ws["_close"] or 0)
                        _tp_key = f"wl_ni_{_ws['_ticker']}"

                        # 세션 초기화 (종목 전환 시 저장값으로 리셋)
                        _saved_tp = int(_we_item.get("target_price") or 0)
                        if _tp_key not in st.session_state:
                            st.session_state[_tp_key] = _saved_tp

                        # 현재가 표시
                        if _cur_price:
                            st.caption(f"현재가 **₩{_cur_price:,}**")

                        # % 프리셋 버튼 (2행 × 3열)
                        st.caption("현재가 대비 목표가 설정")
                        _pct_rows = [[5, 10, 15], [20, 25, 30]]
                        for _pr in _pct_rows:
                            _pr_cols = st.columns(3)
                            for _ci, _pct in enumerate(_pr):
                                with _pr_cols[_ci]:
                                    _calc = int(_cur_price * (1 + _pct / 100)) if _cur_price else 0
                                    if st.button(
                                        f"+{_pct}%\n₩{_calc:,}" if _calc else f"+{_pct}%",
                                        key=f"wl_pct_{_ws['_ticker']}_{_pct}",
                                        use_container_width=True,
                                    ):
                                        st.session_state[_tp_key] = _calc
                                        st.rerun()

                        # 직접 입력 (session_state와 연동)
                        st.caption("또는 직접 입력")
                        _e_target = st.number_input(
                            "목표가(원)", min_value=0, step=100,
                            key=_tp_key, label_visibility="collapsed",
                        )

                        # 괴리율 실시간 표시
                        if _cur_price and _e_target:
                            _gap_pct = (_e_target / _cur_price - 1) * 100
                            _gap_color = "#16a34a" if _gap_pct >= 0 else "#dc2626"
                            st.markdown(
                                f'<div style="text-align:center;font-size:13px;'
                                f'color:{_gap_color};font-weight:600;margin:4px 0">'
                                f'목표까지 {_gap_pct:+.1f}% &nbsp;·&nbsp; ₩{int(_e_target):,}</div>',
                                unsafe_allow_html=True,
                            )

                        _e_group = st.text_input("그룹", value=_we_item.get("group",""),
                                                  key=f"wl_grp_{_ws['_ticker']}")
                        _e_note  = st.text_input("메모", value=_we_item.get("note",""),
                                                  key=f"wl_note_{_ws['_ticker']}")

                        _ef1, _ef2 = st.columns(2)
                        if _ef1.button("💾 저장", key=f"wl_save_{_ws['_ticker']}",
                                       use_container_width=True):
                            for _wi in _wl_items:
                                if _wi["ticker"] == _ws["_ticker"]:
                                    _wi["target_price"] = int(_e_target)
                                    _wi["group"]  = _e_group.strip()
                                    _wi["note"]   = _e_note.strip()
                                    break
                            _save_watchlist(_wl_items)
                            del st.session_state[_tp_key]
                            st.rerun()
                        if _ef2.button("🗑️ 삭제", key=f"wl_del_{_ws['_ticker']}",
                                       use_container_width=True):
                            _wl_remove(_ws["_ticker"])
                            st.rerun()

    st.divider()

    st.divider()
    # ─── SECTION: 미래 산업 섹터 ─────────────────────────────────────────────────

    st.markdown('<a id="future-sector"></a>', unsafe_allow_html=True)
    st.header("🚀 미래 산업 섹터")

    _AI_POWER_PATH    = BASE_DIR / "data" / "ai_power_sector.json"
    _AI_SECURITY_PATH = BASE_DIR / "data" / "ai_security_sector.json"
    _SIZE_COLOR = {"대형": "#1e40af", "중형": "#065f46", "소형": "#92400e"}

    def _load_ai_power() -> dict:
        if not _AI_POWER_PATH.exists():
            return {}
        try:
            return json.loads(_AI_POWER_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_ai_power(data: dict) -> None:
        _AI_POWER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_ai_security() -> dict:
        if not _AI_SECURITY_PATH.exists():
            return {}
        try:
            return json.loads(_AI_SECURITY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _render_stock_cards_and_table(stocks: list[dict], table_key_prefix: str) -> None:
        """종목 카드 + 재무·가격 테이블 렌더러 (AI 섹터 공통)."""
        _cols = st.columns(2)
        for _si, _stk in enumerate(stocks):
            _tk = _stk["ticker"]
            if not _tk:
                continue
            _mkt = (market_data_raw or {}).get(_tk, {})
            _close  = _mkt.get("close")
            _chg    = _mkt.get("change_rate")
            _mktcap = _mkt.get("market_cap")
            _close_str = f"₩{_close:,.0f}" if _close else "—"
            _chg_str   = f"{_chg:+.2f}%" if _chg is not None else "—"
            _chg_color = "#15803d" if (_chg or 0) >= 0 else "#b91c1c"
            _cap_str   = f"{_mktcap/100000000:,.0f}억" if _mktcap else "—"
            _sz_color  = _SIZE_COLOR.get(_stk.get("size", ""), "#374151")
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
  <div style="font-size:12px;color:#374151;line-height:1.5">{_stk.get('thesis','')}</div>
</div>""",
                    unsafe_allow_html=True,
                )
        valid_tickers = [s["ticker"] for s in stocks if s.get("ticker")]
        stk_name_map  = {s["ticker"]: s["name"] for s in stocks if s.get("ticker")}
        if market_data_raw and valid_tickers:
            st.markdown("##### 📊 재무·가격 데이터")
            with st.spinner("데이터 조회 중..."):
                _rows = _build_stock_rows(valid_tickers)
            for _row in _rows:
                if _row["ticker"] in stk_name_map:
                    _row["name"] = stk_name_map[_row["ticker"]]
            _render_stock_table(_rows, height=len(valid_tickers) * 38 + 60, table_key=table_key_prefix)
        else:
            st.caption("⚠️ 재무·가격 데이터 없음 — 파이프라인 실행 후 표시됩니다.")

    _future_sub_tabs = st.tabs(["⚡ AI 전력 인프라", "🛡️ AI 보안"])

    # ── ① AI 전력 인프라 ─────────────────────────────────────────────────────────
    with _future_sub_tabs[0]:
        _ap = _load_ai_power()
        if not _ap:
            st.warning("data/ai_power_sector.json 파일이 없습니다.")
        else:
            st.caption(
                f"**{_ap.get('description', '')}**  \n"
                f"최종 수정: {_ap.get('last_updated', '—')} | 총 {sum(len(s['stocks']) for s in _ap.get('stages', []))}개 종목"
            )
            _stage_tabs = st.tabs([
                f"{s['emoji']} {s['stage']}단계: {s['name']}"
                for s in _ap.get("stages", [])
            ])
            for _st_tab, _stage in zip(_stage_tabs, _ap.get("stages", [])):
                with _st_tab:
                    st.caption(f"**{_stage['description']}**")
                    st.markdown("")
                    _kr_tab, _us_tab = st.tabs(["🇰🇷 국내 관련주", "🇺🇸 미국 관련주"])
                    with _kr_tab:
                        _render_stock_cards_and_table(_stage["stocks"], f"aip_{_stage['key']}")
                    with _us_tab:
                        _us_stks = _stage.get("us_stocks", [])
                        if not _us_stks:
                            st.info("미국 관련주 데이터가 없습니다.")
                        elif not _YF_OK:
                            st.warning("yfinance 미설치 — `pip install yfinance` 후 재시작하세요.")
                        else:
                            _us_cols = st.columns(2)
                            _us_sel_state_key = f"aip_us_sel_{_stage['key']}"
                            for _ui, _ustk in enumerate(_us_stks):
                                _utk = _ustk["ticker"]
                                _upd = _yf_price(_utk)
                                _ulast = _upd.get("last")
                                _uchg  = _upd.get("chg")
                                _ucur  = _upd.get("currency", "USD")
                                _ulast_str = f"{_ucur} {_ulast:,.2f}" if _ulast else "—"
                                _uchg_str  = f"{_uchg:+.2f}%" if _uchg is not None else "—"
                                _uchg_col  = "#15803d" if (_uchg or 0) >= 0 else "#b91c1c"
                                with _us_cols[_ui % 2]:
                                    st.markdown(
                                        f"""<div style="border:1px solid #dbeafe;border-radius:8px;padding:12px 14px;margin-bottom:10px;background:#f8faff">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
    <span style="font-weight:700;font-size:15px">{_ustk['name']}</span>
    <span style="background:#1e40af;color:#fff;font-size:11px;padding:2px 7px;border-radius:10px">{_utk}</span>
  </div>
  <div style="display:flex;gap:16px;font-size:13px;margin-bottom:8px">
    <span>현재가 <b>{_ulast_str}</b></span>
    <span style="color:{_uchg_col}"><b>{_uchg_str}</b></span>
  </div>
  <div style="font-size:12px;color:#374151;line-height:1.5">{_ustk.get('thesis','')}</div>
</div>""",
                                        unsafe_allow_html=True,
                                    )
                            _us_chart_ticker = st.selectbox(
                                "차트 조회",
                                options=[s["ticker"] for s in _us_stks],
                                format_func=lambda t: next((s["name"] for s in _us_stks if s["ticker"] == t), t),
                                key=f"aip_us_chart_{_stage['key']}",
                            )
                            if _us_chart_ticker:
                                _us_chart_name = next((s["name"] for s in _us_stks if s["ticker"] == _us_chart_ticker), _us_chart_ticker)
                                _render_us_chart(_us_chart_ticker, _us_chart_name, key_prefix=f"aip_{_stage['key']}")

                    with st.expander(f"✏️ {_stage['name']} 단계 종목 편집"):
                        st.markdown("**종목 삭제**")
                        _del_names = [s["name"] for s in _stage["stocks"]]
                        _to_del = st.multiselect("삭제할 종목 선택", _del_names, key=f"ai_del_{_stage['key']}")
                        st.markdown("**종목 추가**")
                        _c1, _c2, _c3 = st.columns([2, 2, 1])
                        _new_name   = _c1.text_input("종목명", key=f"ai_add_name_{_stage['key']}", placeholder="예: 현대건설")
                        _new_ticker = _c2.text_input("티커 (6자리)", key=f"ai_add_ticker_{_stage['key']}", placeholder="000720")
                        _new_size   = _c3.selectbox("구분", ["대형", "중형", "소형"], key=f"ai_add_size_{_stage['key']}")
                        _new_thesis = st.text_area("투자 논리", key=f"ai_add_thesis_{_stage['key']}", placeholder="핵심 수혜 이유")
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

    # ── ② AI 보안 ────────────────────────────────────────────────────────────────
    with _future_sub_tabs[1]:
        _asec = _load_ai_security()
        if not _asec:
            st.warning("data/ai_security_sector.json 파일이 없습니다.")
        else:
            st.caption(
                f"**{_asec.get('description', '')}**  \n"
                f"최종 수정: {_asec.get('last_updated', '—')} | 총 {sum(len(g['stocks']) for g in _asec.get('groups', []))}개 종목"
            )
            _sec_group_tabs = st.tabs([
                f"{g['emoji']} {g['name']}" for g in _asec.get("groups", [])
            ])
            for _sg_tab, _grp in zip(_sec_group_tabs, _asec.get("groups", [])):
                with _sg_tab:
                    st.caption(f"**{_grp['description']}**")
                    st.markdown("")
                    _sec_kr_tab, _sec_us_tab = st.tabs(["🇰🇷 국내 관련주", "🇺🇸 미국 관련주"])
                    with _sec_kr_tab:
                        _render_stock_cards_and_table(_grp["stocks"], f"aisec_{_grp['group_key']}")
                    with _sec_us_tab:
                        _sec_us_stks = _grp.get("us_stocks", [])
                        if not _sec_us_stks:
                            st.info("미국 관련주 데이터가 없습니다.")
                        elif not _YF_OK:
                            st.warning("yfinance 미설치 — `pip install yfinance` 후 재시작하세요.")
                        else:
                            _sec_us_cols = st.columns(2)
                            for _sui, _sustk in enumerate(_sec_us_stks):
                                _sutk = _sustk["ticker"]
                                _supd = _yf_price(_sutk)
                                _sulast = _supd.get("last")
                                _suchg  = _supd.get("chg")
                                _sucur  = _supd.get("currency", "USD")
                                _sulast_str = f"{_sucur} {_sulast:,.2f}" if _sulast else "—"
                                _suchg_str  = f"{_suchg:+.2f}%" if _suchg is not None else "—"
                                _suchg_col  = "#15803d" if (_suchg or 0) >= 0 else "#b91c1c"
                                with _sec_us_cols[_sui % 2]:
                                    st.markdown(
                                        f"""<div style="border:1px solid #dbeafe;border-radius:8px;padding:12px 14px;margin-bottom:10px;background:#f8faff">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
    <span style="font-weight:700;font-size:15px">{_sustk['name']}</span>
    <span style="background:#1e40af;color:#fff;font-size:11px;padding:2px 7px;border-radius:10px">{_sutk}</span>
  </div>
  <div style="display:flex;gap:16px;font-size:13px;margin-bottom:8px">
    <span>현재가 <b>{_sulast_str}</b></span>
    <span style="color:{_suchg_col}"><b>{_suchg_str}</b></span>
  </div>
  <div style="font-size:12px;color:#374151;line-height:1.5">{_sustk.get('thesis','')}</div>
</div>""",
                                        unsafe_allow_html=True,
                                    )
                            _sec_us_chart_ticker = st.selectbox(
                                "차트 조회",
                                options=[s["ticker"] for s in _sec_us_stks],
                                format_func=lambda t: next((s["name"] for s in _sec_us_stks if s["ticker"] == t), t),
                                key=f"aisec_us_chart_{_grp['group_key']}",
                            )
                            if _sec_us_chart_ticker:
                                _sec_us_chart_name = next((s["name"] for s in _sec_us_stks if s["ticker"] == _sec_us_chart_ticker), _sec_us_chart_ticker)
                                _render_us_chart(_sec_us_chart_ticker, _sec_us_chart_name, key_prefix=f"aisec_{_grp['group_key']}")

    st.divider()

    # ─── 🇺🇸 미국 관심종목 ────────────────────────────────────────────────────────

    st.header("🇺🇸 미국 관심종목")

    if not _YF_OK:
        st.warning("yfinance 미설치 — `pip install yfinance` 후 재시작하세요.")
    else:
        _us_wl_items = _load_us_watchlist()

        _uwc1, _uwc2, _uwc3 = st.columns([3, 2, 2])
        with _uwc3:
            with st.popover("➕ 미국 종목 추가", use_container_width=True):
                _u_add_tkr  = st.text_input("티커 (예: NVDA, AAPL)", key="us_wl_add_tkr",
                                              placeholder="NVDA").upper().strip()
                _u_add_name = st.text_input("종목명 (선택)", key="us_wl_add_name",
                                             placeholder="엔비디아")
                _u_add_note = st.text_input("메모", key="us_wl_add_note", placeholder="관심 이유")
                if st.button("⭐ 추가", key="us_wl_add_btn", use_container_width=True):
                    if _u_add_tkr:
                        _display_name = _u_add_name.strip() or _u_add_tkr
                        _ok = _us_wl_add(_u_add_tkr, _display_name, note=_u_add_note.strip())
                        if _ok:
                            st.success(f"{_display_name} 추가됨!")
                            st.rerun()
                        else:
                            st.warning("이미 추가된 종목입니다.")

        with _uwc2:
            _us_sort = st.selectbox("정렬", ["추가일↓", "전일등락↓", "목표괴리↓"],
                                     key="us_wl_sort", label_visibility="collapsed")

        if not _us_wl_items:
            st.info("미국 관심 종목이 없습니다. ➕ 버튼으로 추가하세요. (예: NVDA, AAPL, MSFT)")
        else:
            # 실시간 가격 조회
            _us_rows = []
            for _uw in _us_wl_items:
                _utk = _uw["ticker"]
                _upd = _yf_price(_utk)
                _u_last   = _upd.get("last")
                _u_chg    = _upd.get("chg")
                _u_cur    = _upd.get("currency", "USD")
                _u_target = _uw.get("target_price") or 0.0
                _u_gap    = round((_u_target / _u_last - 1) * 100, 2) if _u_last and _u_target else None
                _us_rows.append({
                    "_ticker":  _utk, "_name": _uw["name"],
                    "_last":    _u_last, "_chg":  _u_chg, "_cur": _u_cur,
                    "_target":  _u_target, "_gap": _u_gap,
                    "_note":    _uw.get("note",""), "_added": _uw.get("added_date",""),
                })

            # 정렬
            if _us_sort == "전일등락↓":
                _us_rows.sort(key=lambda r: (r["_chg"] or -999), reverse=True)
            elif _us_sort == "목표괴리↓":
                _us_rows.sort(key=lambda r: (r["_gap"] or -999), reverse=True)
            else:
                _us_rows.sort(key=lambda r: r["_added"], reverse=True)

            # 테이블
            _us_disp = pd.DataFrame({
                "종목명":      [r["_name"]   for r in _us_rows],
                "티커":        [r["_ticker"] for r in _us_rows],
                "현재가":      [f"{r['_cur']} {r['_last']:,.2f}" if r["_last"] else "—" for r in _us_rows],
                "전일(%)":     [round(r["_chg"], 2) if r["_chg"] is not None else None for r in _us_rows],
                "목표가":      [f"{r['_cur']} {r['_target']:,.2f}" if r["_target"] else "—" for r in _us_rows],
                "목표괴리(%)": [r["_gap"] for r in _us_rows],
                "메모":        [r["_note"]   for r in _us_rows],
                "추가일":      [r["_added"]  for r in _us_rows],
            })
            _us_col_names = list(_us_disp.columns)

            def _us_style(row):
                styles = [""] * len(_us_col_names)
                _apply_chg_style(styles, _us_col_names, "전일(%)",     row["전일(%)"])
                _apply_chg_style(styles, _us_col_names, "목표괴리(%)", row["목표괴리(%)"])
                return styles

            _us_ev = st.dataframe(
                _us_disp.style.apply(_us_style, axis=1),
                use_container_width=True,
                height=min(500, 80 + len(_us_rows) * 38),
                on_select="rerun", selection_mode="single-row",
                key="us_wl_tbl", hide_index=True,
            )
            _us_sel = (_us_ev.selection.rows or []) if hasattr(_us_ev, "selection") else []

            if _us_sel and _us_sel[0] < len(_us_rows):
                _uws = _us_rows[_us_sel[0]]
                _u_exp1, _u_exp2 = st.columns([3, 1])
                with _u_exp1:
                    with st.expander(f"📈 {_uws['_name']} ({_uws['_ticker']}) 차트", expanded=True):
                        _render_us_chart(_uws["_ticker"], _uws["_name"], key_prefix="wl")
                with _u_exp2:
                    with st.expander("✏️ 편집 / 삭제", expanded=True):
                        _u_we = next((w for w in _us_wl_items if w["ticker"] == _uws["_ticker"]), None)
                        if _u_we:
                            _u_cur_price = _uws["_last"] or 0.0
                            _u_tp_key = f"us_ni_{_uws['_ticker']}"
                            if _u_tp_key not in st.session_state:
                                st.session_state[_u_tp_key] = float(_u_we.get("target_price") or 0.0)
                            if _u_cur_price:
                                st.caption(f"현재가 **{_uws['_cur']} {_u_cur_price:,.2f}**")
                            # % 프리셋
                            st.caption("현재가 대비 목표가")
                            _u_pct_rows = [[5, 10, 15], [20, 25, 30]]
                            for _u_pr in _u_pct_rows:
                                _u_pr_cols = st.columns(3)
                                for _u_ci, _u_pct in enumerate(_u_pr):
                                    with _u_pr_cols[_u_ci]:
                                        _u_calc = round(_u_cur_price * (1 + _u_pct / 100), 2) if _u_cur_price else 0.0
                                        if st.button(
                                            f"+{_u_pct}%\n{_u_calc:,.1f}" if _u_calc else f"+{_u_pct}%",
                                            key=f"us_pct_{_uws['_ticker']}_{_u_pct}",
                                            use_container_width=True,
                                        ):
                                            st.session_state[_u_tp_key] = _u_calc
                                            st.rerun()
                            st.caption("또는 직접 입력")
                            _u_e_target = st.number_input(
                                "목표가", min_value=0.0, step=1.0,
                                key=_u_tp_key, label_visibility="collapsed",
                            )
                            if _u_cur_price and _u_e_target:
                                _u_gap_pct = (_u_e_target / _u_cur_price - 1) * 100
                                _u_gc = "#16a34a" if _u_gap_pct >= 0 else "#dc2626"
                                st.markdown(
                                    f'<div style="text-align:center;font-size:13px;color:{_u_gc};'
                                    f'font-weight:600;margin:4px 0">목표까지 {_u_gap_pct:+.1f}%</div>',
                                    unsafe_allow_html=True,
                                )
                            _u_note_e = st.text_input("메모", value=_u_we.get("note",""),
                                                       key=f"us_note_{_uws['_ticker']}")
                            _uf1, _uf2 = st.columns(2)
                            if _uf1.button("💾 저장", key=f"us_save_{_uws['_ticker']}",
                                           use_container_width=True):
                                for _uwi in _us_wl_items:
                                    if _uwi["ticker"] == _uws["_ticker"]:
                                        _uwi["target_price"] = float(_u_e_target)
                                        _uwi["note"] = _u_note_e.strip()
                                        break
                                _save_us_watchlist(_us_wl_items)
                                del st.session_state[_u_tp_key]
                                st.rerun()
                            if _uf2.button("🗑️ 삭제", key=f"us_del_{_uws['_ticker']}",
                                           use_container_width=True):
                                _us_wl_remove(_uws["_ticker"])
                                st.rerun()

    st.divider()


with _tab_market:
    # ─── SECTION 0b: 오늘의 액션 카드 ───────────────────────────���────────────────

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
    st.header("🌟 뉴스·모멘텀 주목 섹터")

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


with _tab_screen:
    # ─── 종목 검색 ────────────────────────────────────────────────────────────────

    st.markdown("### 🔍 종목 검색")
    _sq_col1, _sq_col2 = st.columns([4, 1])
    with _sq_col1:
        st.text_input(
            "종목명 또는 티커",
            key="stock_search_query",
            placeholder="예: 삼성전자, 005930, SK하이닉스",
            label_visibility="collapsed",
        )

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

            _wl_now = _load_watchlist()
            _wl_tickers_now = {w["ticker"] for w in _wl_now}
            _not_in_wl = [(tkr, info) for tkr, info in _matches[:20] if tkr not in _wl_tickers_now]
            if _not_in_wl:
                st.caption("☆ 관심 종목에 추가")
                _wl_add_cols = st.columns(min(5, len(_not_in_wl)))
                for _ci, (tkr, info) in enumerate(_not_in_wl[:5]):
                    nm = info.get("name", tkr)
                    with _wl_add_cols[_ci]:
                        if st.button(f"☆ {nm}", key=f"wl_srch_{tkr}", use_container_width=True):
                            _wl_add(tkr, nm)
                            st.rerun()

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
                _sel_tkr = next((tkr for tkr, info in _matches[:20] if info.get("name", tkr) == _sel_name), None)
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
    elif _sq:
        st.info("시장 데이터 없음 — 파이프라인을 실행하세요.")

    st.divider()

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

    # ─── 🇺🇸 미국 종목 검색 ──────────────────────────────────────────────────────

    st.header("🇺🇸 미국 종목 검색")

    _US_PRESETS: dict[str, list[str]] = {
        "🤖 AI·반도체": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "ARM", "AMAT", "MU"],
        "⚡ AI 전력": ["GEV", "CEG", "ETN", "VRT", "EQIX", "DLR", "PWR", "AME"],
        "🛡️ 사이버보안": ["CRWD", "PANW", "ZS", "FTNT", "S", "OKTA", "CYBR", "NET"],
        "🇺🇸 빅테크": ["MSFT", "AAPL", "GOOGL", "META", "AMZN", "TSLA", "TSM", "ASML"],
    }

    if not _YF_OK:
        st.warning("yfinance 미설치 — `pip install yfinance` 후 재시작하세요.")
    else:
        _usc1, _usc2 = st.columns([3, 2])
        with _usc1:
            _us_search_in = st.text_input(
                "티커 직접 입력",
                placeholder="예: NVDA, AAPL, TSM (쉼표 구분)",
                key="us_screen_search", label_visibility="collapsed",
            )
        with _usc2:
            _us_preset_sel = st.selectbox(
                "프리셋",
                ["직접 입력"] + list(_US_PRESETS.keys()),
                key="us_screen_preset", label_visibility="collapsed",
            )

        if _us_preset_sel != "직접 입력":
            _us_query_tickers = _US_PRESETS[_us_preset_sel]
        elif _us_search_in.strip():
            _us_query_tickers = [t.strip().upper() for t in _us_search_in.replace(",", " ").split() if t.strip()][:12]
        else:
            _us_query_tickers = []

        if _us_query_tickers:
            @st.cache_data(show_spinner="가격 조회 중...", ttl=180)
            def _us_batch_price(tickers_tuple: tuple) -> list[dict]:
                rows = []
                for _tk in tickers_tuple:
                    pd_ = _yf_price(_tk)
                    rows.append({
                        "ticker":    _tk,
                        "last":      pd_.get("last"),
                        "chg":       pd_.get("chg"),
                        "currency":  pd_.get("currency", "USD"),
                        "high52":    pd_.get("high52"),
                        "low52":     pd_.get("low52"),
                        "mktcap":    pd_.get("mktcap"),
                    })
                return rows

            _us_prices = _us_batch_price(tuple(_us_query_tickers))

            # ── 가격 카드 그리드 ──────────────────────────────────────────────────
            _us_card_cols = st.columns(min(4, len(_us_prices)))
            for _ui, _uprow in enumerate(_us_prices):
                _utk2 = _uprow["ticker"]
                _ulast = _uprow.get("last")
                _uchg  = _uprow.get("chg")
                _ucur  = _uprow.get("currency", "USD")
                _uc2   = "#15803d" if (_uchg or 0) >= 0 else "#b91c1c"
                _u52h  = _uprow.get("high52")
                _u52l  = _uprow.get("low52")
                _u_pos_str = ""
                if _ulast and _u52h and _u52l and _u52h > _u52l:
                    _u_pos_pct = (_ulast - _u52l) / (_u52h - _u52l) * 100
                    _u_pos_str = f"52주 {_u_pos_pct:.0f}%"
                with _us_card_cols[_ui % len(_us_card_cols)]:
                    st.markdown(
                        f"""<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;margin-bottom:8px">
  <div style="font-weight:700;font-size:14px">{_utk2}</div>
  <div style="color:#6b7280;font-size:11px;margin-bottom:4px">{_u_pos_str}</div>
  <div style="font-size:17px;font-weight:700">{f"{_ucur} {_ulast:,.2f}" if _ulast else "—"}</div>
  <div style="color:{_uc2};font-size:13px;font-weight:600">{f"{_uchg:+.2f}%" if _uchg is not None else "—"}</div>
</div>""",
                        unsafe_allow_html=True,
                    )

            # ── 요약 테이블 ──────────────────────────────────────────────────────
            _us_tbl_rows = []
            for _uprow in _us_prices:
                _utk2   = _uprow["ticker"]
                _ulast  = _uprow.get("last")
                _uchg   = _uprow.get("chg")
                _ucur   = _uprow.get("currency", "USD")
                _umktcap = _uprow.get("mktcap")
                _us_tbl_rows.append({
                    "티커":       _utk2,
                    "현재가":     f"{_ucur} {_ulast:,.2f}" if _ulast else "—",
                    "전일(%)" :   round(_uchg, 2) if _uchg is not None else None,
                    "52주고":     f"{_uprow.get('high52'):,.2f}" if _uprow.get("high52") else "—",
                    "52주저":     f"{_uprow.get('low52'):,.2f}"  if _uprow.get("low52")  else "—",
                    "시가총액":   f"${_umktcap/1e9:.1f}B" if _umktcap else "—",
                })
            _us_tbl_df  = pd.DataFrame(_us_tbl_rows)
            _us_tbl_cols = list(_us_tbl_df.columns)

            def _us_tbl_style(row):
                styles = [""] * len(_us_tbl_cols)
                _apply_chg_style(styles, _us_tbl_cols, "전일(%)", row["전일(%)"])
                return styles

            _us_tbl_ev = st.dataframe(
                _us_tbl_df.style.apply(_us_tbl_style, axis=1),
                use_container_width=True, hide_index=True,
                height=min(450, 60 + len(_us_tbl_rows) * 38),
                on_select="rerun", selection_mode="single-row",
                key="us_screen_tbl",
            )
            _us_tbl_sel = (_us_tbl_ev.selection.rows or []) if hasattr(_us_tbl_ev, "selection") else []
            if _us_tbl_sel and _us_tbl_sel[0] < len(_us_prices):
                _u_sel_tk = _us_prices[_us_tbl_sel[0]]["ticker"]
                with st.expander(f"📈 {_u_sel_tk} 상세 차트", expanded=True):
                    _render_us_chart(_u_sel_tk, _u_sel_tk, key_prefix="screen")

                # 관심 추가 버튼
                _us_wl_now = _load_us_watchlist()
                _already_us = any(w["ticker"] == _u_sel_tk for w in _us_wl_now)
                if not _already_us:
                    if st.button(f"☆ {_u_sel_tk} 미국 관심종목 추가", key=f"us_add_from_screen_{_u_sel_tk}"):
                        _us_wl_add(_u_sel_tk, _u_sel_tk)
                        st.rerun()
                else:
                    st.caption(f"⭐ {_u_sel_tk} 이미 관심종목에 추가됨")
        else:
            st.info("프리셋을 선택하거나 티커를 직접 입력하세요.")
            st.caption("티커 예시: NVDA (엔비디아) · AAPL (애플) · TSM (TSMC) · 7203.T (도요타)")

    st.divider()


with _tab_news:
    # ─── SECTION 7: 뉴스 검색 ─────────────────────────────────────────────────────

    st.markdown('<a id="news"></a>', unsafe_allow_html=True)
    st.header("📰 뉴스 검색")

    _news_path = OUTPUT_DIR / "step1_news_raw.json"

    # 새로고침 버튼 ── fetch_news → score_sentiment → extract_themes → build_sector_scorecard 순 실행
    _NP_DIR = BASE_DIR / ".claude/skills/news-preprocessor/scripts"
    _NEWS_PIPELINE = [
        (BASE_DIR / ".claude/skills/data-collector/scripts/fetch_news.py",  "뉴스 수집"),
        (_NP_DIR / "deduplicate.py",           "중복 제거"),
        (_NP_DIR / "extract_sentences.py",     "핵심 문장 추출"),
        (_NP_DIR / "score_sentiment.py",       "감성 분석"),
        (_NP_DIR / "build_llm_input.py",       "섹터 태깅 & 뉴스 저장"),
        (_NP_DIR / "extract_themes.py",        "테마 추출"),
        (_NP_DIR / "build_sector_scorecard.py","섹터 점수 산출"),
    ]
    _nc1, _nc2 = st.columns([3, 1])
    with _nc2:
        if st.button("🔄 뉴스 + 섹터 갱신", use_container_width=True, key="news_refresh_btn"):
            import subprocess as _sp
            _errors = []
            _progress = st.progress(0, text="시작 중…")
            for _pi, (_script, _label) in enumerate(_NEWS_PIPELINE):
                _progress.progress((_pi) / len(_NEWS_PIPELINE), text=f"{_label} 중…")
                _r = _sp.run([sys.executable, str(_script)],
                             capture_output=True, text=True, timeout=180,
                             cwd=str(BASE_DIR))
                if _r.returncode != 0:
                    _errors.append(f"{_label}: {_r.stderr[-200:]}")
            _progress.progress(1.0, text="완료")
            if _errors:
                st.error("일부 실패:\n" + "\n".join(_errors))
            else:
                st.success("뉴스 + 섹터 점수 업데이트 완료 — 페이지를 새로고침하면 반영됩니다.")
                st.rerun()

    # 뉴스 로드
    _raw_articles: list[dict] = []
    if _news_path.exists():
        try:
            _raw_articles = json.loads(_news_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    with _nc1:
        if _raw_articles:
            _last_pub = max((a.get("pub_date","") for a in _raw_articles), default="—")
            st.caption(f"총 {len(_raw_articles)}건 · 최신 기사: {_last_pub[:16]}")
        else:
            st.caption("뉴스 데이터 없음 — 새로고침을 눌러 수집하세요.")

    if _raw_articles:
        # 검색 + 소스 필터
        _nf1, _nf2, _nf3 = st.columns([3, 2, 1])
        with _nf1:
            _news_kw = st.text_input("키워드 검색", placeholder="예: 반도체, 금리, 환율",
                                      key="news_kw", label_visibility="collapsed")
        _source_map = {
            "yonhap_info": "연합인포맥스", "investing_kr": "인베스팅",
            "newsis_eco": "뉴시스", "hankyung": "한국경제",
            "maeil_eco": "매일경제", "yonhap_eco": "연합뉴스",
            "edaily": "이데일리",
        }
        _all_sources = sorted({a.get("source","") for a in _raw_articles if a.get("source")})
        with _nf2:
            _sel_sources = st.multiselect(
                "소스 필터", options=_all_sources,
                format_func=lambda x: _source_map.get(x, x),
                default=[], key="news_src_filter", label_visibility="collapsed",
                placeholder="전체 소스"
            )
        with _nf3:
            _news_days = st.selectbox("기간", ["오늘", "2일", "전체"], index=0,
                                       key="news_days_filter", label_visibility="collapsed")

        # 필터 적용
        from datetime import timedelta as _td
        _today = datetime.now().date()
        _cutoff = {
            "오늘": _today,
            "2일":  _today - _td(days=1),
            "전체": None,
        }[_news_days]

        _filtered = []
        for _art in _raw_articles:
            # 날짜 필터
            if _cutoff:
                try:
                    _art_date = datetime.strptime(_art.get("pub_date","")[:10], "%Y-%m-%d").date()
                    if _art_date < _cutoff:
                        continue
                except Exception:
                    pass
            # 소스 필터
            if _sel_sources and _art.get("source","") not in _sel_sources:
                continue
            # 키워드 필터
            if _news_kw:
                _kw_lower = _news_kw.lower()
                _haystack = (_art.get("title","") + " " + _art.get("content","")).lower()
                if _kw_lower not in _haystack:
                    continue
            _filtered.append(_art)

        # 정렬: 최신순
        _filtered.sort(key=lambda x: x.get("pub_date",""), reverse=True)

        st.caption(f"{len(_filtered)}건 표시 중" + (f"  ·  키워드: **{_news_kw}**" if _news_kw else ""))

        # 기사 목록
        if not _filtered:
            st.info("조건에 맞는 기사가 없습니다.")
        else:
            # 페이지네이션
            _PAGE = 20
            _total_pages = max(1, (len(_filtered) + _PAGE - 1) // _PAGE)
            _page_idx = st.number_input("페이지", min_value=1, max_value=_total_pages,
                                         value=1, step=1, key="news_page") - 1
            _page_arts = _filtered[_page_idx * _PAGE : (_page_idx + 1) * _PAGE]

            for _art in _page_arts:
                _src_label = _source_map.get(_art.get("source",""), _art.get("source",""))
                _pub = _art.get("pub_date","")[:16]
                _title = _art.get("title","(제목 없음)")
                _content = _art.get("content","").strip()
                _url = _art.get("url","")

                # 키워드 하이라이트 (HTML)
                def _hl(text: str, kw: str) -> str:
                    if not kw or not text:
                        return text
                    import re as _re
                    return _re.sub(f"({_re.escape(kw)})",
                                   r'<mark style="background:#fef08a">\1</mark>',
                                   text, flags=_re.IGNORECASE)

                _title_hl = _hl(_title, _news_kw)
                _excerpt = _content[:200] + ("…" if len(_content) > 200 else "")
                _excerpt_hl = _hl(_excerpt, _news_kw)

                with st.expander(f"[{_src_label}] {_title} — {_pub}", expanded=False):
                    st.markdown(
                        f'<div style="font-size:14px;line-height:1.6">'
                        f'<b>{_title_hl}</b><br>'
                        f'<span style="color:#6b7280;font-size:12px">{_src_label} · {_pub}</span><br><br>'
                        f'{_excerpt_hl}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if _url:
                        st.markdown(f"[원문 보기 →]({_url})")

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


with _tab_trade:
    # ─── SECTION 8: 트레이드 노트 ─────────────────────────────────────────────────

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



with _tab_system:
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
