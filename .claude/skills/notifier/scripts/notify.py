#!/usr/bin/env python3
"""
STEP 4 — Telegram 알림 (오늘의 액션 카드)
환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR   = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

KRX_TO_NEWS = {
    "전기·전자": "반도체", "IT 서비스": "IT서비스",
    "제약": "바이오", "의료·정밀기기": "바이오",
    "전기·가스": "에너지", "전기·가스·수도": "에너지",
    "운송장비·부품": "자동차", "금속": "철강",
    "화학": "화학", "금융": "금융", "은행": "금융",
    "보험": "금융", "증권": "금융", "기타금융": "금융",
    "건설": "건설", "유통": "유통", "통신": "통신",
}


def _load(fname: str) -> dict:
    p = OUTPUT_DIR / fname
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _fmt(v, decimals=1, suffix="") -> str:
    if v is None:
        return "N/A"
    r = round(float(v), decimals)
    return (f"{int(r):,}" if r == int(r) else f"{r:,}") + suffix


def _send(text: str) -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    return resp.ok


def build_message(rankings: dict, ratios: dict, themes: dict) -> str:
    today = date.today().strftime("%Y년 %m월 %d일")

    # 1d 편입/이탈 수집
    new_entries: list[tuple[str, str, str]] = []
    removed:     list[tuple[str, str, str]] = []
    for sector, v in rankings.items():
        ch       = v.get("changes", {}).get("1d", {})
        top10map = {it["ticker"]: it.get("name", it["ticker"]) for it in v.get("top10", [])}
        for t in ch.get("new_entries", []):
            new_entries.append((top10map.get(t, t), sector, t))
        for t in ch.get("removed", []):
            removed.append((top10map.get(t, t), sector, t))

    # 주목 섹터
    highlighted = [
        krx for krx, v in rankings.items()
        if themes.get(KRX_TO_NEWS.get(krx, krx), {}).get("highlight")
    ]

    lines: list[str] = [f"📈 *Korea Stock Agent*\n_{today}_\n"]

    # 액션 카드
    if new_entries:
        lines.append(f"🟢 *매수 검토 {len(new_entries)}종목*")
        for name, sector, ticker in new_entries:
            r = ratios.get(ticker, {})
            per_s  = _fmt(r.get("per"), 1)
            roe_s  = _fmt(r.get("roe"), 1, "%")
            debt_s = _fmt(r.get("debt_ratio"), 0, "%")
            lines.append(
                f"• *{name}* \\({sector}\\)\n"
                f"  PER {per_s} · ROE {roe_s} · 부채비율 {debt_s}"
            )
        lines.append("")

    if removed:
        lines.append(f"🔴 *매도 검토 {len(removed)}종목*")
        for name, sector, _ in removed:
            lines.append(f"• *{name}* \\({sector}\\) — Top10 이탈")
        lines.append("")

    if not new_entries and not removed:
        lines.append("ℹ️ 오늘은 편입/이탈 종목 없음\n")

    # 주목 섹터
    if highlighted:
        lines.append(f"⭐ *주목 섹터*: {', '.join(highlighted[:6])}")

    # 계절성 시그널
    month = date.today().month
    season = "공격 시즌 \\(11~4월\\)" if month in [11,12,1,2,3,4] else "방어 시즌 \\(5~10월\\)"
    lines.append(f"\n📅 현재: {season}")

    lines.append("\n_Korea Stock Agent 자동 알림_")
    return "\n".join(lines)


def _build_stop_alerts(market_data_raw: dict) -> list[str]:
    """트레이드 노트 손절 알림 생성."""
    notes_path = BASE_DIR / "data" / "trade_notes.json"
    if not notes_path.exists():
        return []
    try:
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    alerts: list[str] = []
    for name, note in notes.items():
        if note.get("status") != "보유중":
            continue
        cur = market_data_raw.get(note.get("ticker", ""), {}).get("close")
        if cur is None:
            continue
        buy   = note.get("buy_price", 0) or 0
        peak  = note.get("peak_price", buy) or buy
        if buy == 0:
            continue
        pnl_pct = (cur - buy) / buy * 100
        if cur <= buy * 0.9:
            alerts.append(f"🚨 *{name}* 손절선 도달! ({pnl_pct:+.1f}%)")
        elif cur <= peak * 0.9 and peak > buy:
            alerts.append(f"⚠️ *{name}* 추적손절 도달! ({pnl_pct:+.1f}%)")
        elif pnl_pct <= -5:
            alerts.append(f"⚡ *{name}* 손절 임박! ({pnl_pct:+.1f}%)")
    return alerts


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 건너뜀")
        return

    rankings   = _load("step2_sector_rankings.json")
    ratios     = _load("step2_financial_ratios.json")
    themes     = _load("step2_themes.json")
    market_raw = _load("step1_market_data.json")

    if not rankings:
        print("[notify] 데이터 없음 — 건너뜀", file=sys.stderr)
        return

    msg = build_message(rankings, ratios, themes)

    # 손절 알림이 있으면 별도 메시지로 발송
    stop_alerts = _build_stop_alerts(market_raw)
    if stop_alerts:
        stop_msg = "🔴 *손절 알림*\n\n" + "\n".join(stop_alerts)
        _send(stop_msg)
        print(f"[notify] 손절 알림 {len(stop_alerts)}건 발송")

    if _send(msg):
        print(f"[notify] Telegram 발송 완료 ({date.today()})")
    else:
        print("[notify] Telegram 발송 실패", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
