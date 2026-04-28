"""
트레이드 노트 CRUD + 손절 계산
data/trade_notes.json 퍼시스트
"""
from __future__ import annotations

import json
from pathlib import Path

STOP_LOSS_PCT    = 0.10   # 손절선: 매수가 -10%
TRAIL_WARN_PCT   = 0.05   # 손절 임박 경고: -5%


def _notes_path(base_dir: Path) -> Path:
    return base_dir / "data" / "trade_notes.json"


def load_notes(base_dir: Path) -> dict:
    p = _notes_path(base_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_notes(notes: dict, base_dir: Path) -> None:
    p = _notes_path(base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def calc_pnl(note: dict, current_price: float | None) -> dict:
    """
    반환:
      pnl_pct      : 손익률(%)
      pnl_amount   : 평가손익(원)
      stop_price   : 손절가 (매수가 × 0.90)
      trail_price  : 추적손절가 (고점가 × 0.90)
      status_flag  : "정상" | "손절임박" | "손절선도달" | "추적손절도달"
      flag_color   : "green" | "orange" | "red"
    """
    buy_price  = note.get("buy_price",  0) or 0
    quantity   = note.get("quantity",   0) or 0
    peak_price = note.get("peak_price", buy_price) or buy_price

    stop_price  = round(buy_price  * (1 - STOP_LOSS_PCT))
    trail_price = round(peak_price * (1 - STOP_LOSS_PCT))

    if current_price is None or buy_price == 0:
        return {
            "pnl_pct":     None,
            "pnl_amount":  None,
            "stop_price":  stop_price,
            "trail_price": trail_price,
            "status_flag": "시세없음",
            "flag_color":  "gray",
        }

    pnl_pct    = (current_price - buy_price) / buy_price * 100
    pnl_amount = (current_price - buy_price) * quantity

    if current_price <= stop_price:
        flag, color = "손절선도달", "red"
    elif current_price <= trail_price and peak_price > buy_price:
        flag, color = "추적손절도달", "red"
    elif pnl_pct <= -(STOP_LOSS_PCT - TRAIL_WARN_PCT) * 100:
        flag, color = "손절임박", "orange"
    else:
        flag, color = "정상", "green"

    return {
        "pnl_pct":     round(pnl_pct, 2),
        "pnl_amount":  int(pnl_amount),
        "stop_price":  stop_price,
        "trail_price": trail_price,
        "status_flag": flag,
        "flag_color":  color,
    }


def update_peak_prices(notes: dict, market_data: dict) -> tuple[dict, int]:
    """
    현재가 > peak_price 이면 peak_price를 갱신한다.
    반환: (갱신된 notes, 갱신 건수)
    """
    changed = 0
    for name, note in notes.items():
        if note.get("status") != "보유중":
            continue
        ticker = note.get("ticker", "")
        cur = market_data.get(ticker, {}).get("close")
        if cur is None:
            continue
        peak = note.get("peak_price", note.get("buy_price", 0))
        if cur > peak:
            note["peak_price"] = cur
            changed += 1
    return notes, changed


def check_stop_alerts(notes: dict, market_data: dict) -> list[str]:
    """손절 임박/도달 종목 알림 문자열 목록 반환 (Telegram용)."""
    alerts: list[str] = []
    for name, note in notes.items():
        if note.get("status") != "보유중":
            continue
        cur = market_data.get(note.get("ticker", ""), {}).get("close")
        pnl = calc_pnl(note, cur)
        flag = pnl["status_flag"]
        pct  = pnl["pnl_pct"]
        pct_str = f"{pct:+.1f}%" if pct is not None else "?"
        if flag == "손절선도달":
            alerts.append(f"🚨 *{name}* 손절선 도달! ({pct_str})")
        elif flag == "추적손절도달":
            alerts.append(f"⚠️ *{name}* 추적손절 도달! ({pct_str})")
        elif flag == "손절임박":
            alerts.append(f"⚡ *{name}* 손절 임박! ({pct_str})")
    return alerts
