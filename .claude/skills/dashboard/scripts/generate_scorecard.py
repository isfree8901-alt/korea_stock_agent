"""
종목 스코어카드 PDF 생성 (matplotlib PdfPages)
"""
import io
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.backends.backend_pdf import PdfPages

# ─── 한글 폰트 설정 ───────────────────────────────────────────────────────────

def _setup_korean_font() -> None:
    candidates = ["AppleGothic", "Nanum Myeongjo", "Nanum Gothic",
                  "Malgun Gothic", "NanumGothic", "DejaVu Sans"]
    for name in candidates:
        found = fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
        if found and "DejaVu" not in found:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False

_setup_korean_font()

# ─── 색상 팔레트 ──────────────────────────────────────────────────────────────

NAVY    = "#1e3a5f"
GOLD    = "#f59e0b"
GREEN   = "#15803d"
RED     = "#b91c1c"
LGRAY   = "#f3f4f6"
DGRAY   = "#6b7280"

# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _f(v, decimals=1, suffix="", na="N/A") -> str:
    if v is None:
        return na
    try:
        r = round(float(v), decimals)
        base = f"{int(r):,}" if r == int(r) else f"{r:,}"
        return base + suffix
    except (TypeError, ValueError):
        return na


def _color_per(val_str: str, avg: float | None) -> str:
    """PER: 낮을수록 green"""
    try:
        v = float(val_str.replace(",", ""))
        if avg and v < avg * 0.85:
            return GREEN
        if avg and v > avg * 1.15:
            return RED
    except (ValueError, TypeError):
        pass
    return "black"


def _color_roe(val_str: str, avg: float | None) -> str:
    """ROE: 높을수록 green"""
    try:
        v = float(val_str.replace("%", "").replace(",", ""))
        if avg and v > avg * 1.15:
            return GREEN
        if avg and v < avg * 0.85:
            return RED
    except (ValueError, TypeError):
        pass
    return "black"


# ─── 섹터 평균 계산 ────────────────────────────────────────────────────────────

def _sector_avg(top10: list, ratios: dict) -> dict:
    fields = ["per", "pbr", "roe", "debt_ratio", "revenue_growth"]
    vals: dict[str, list] = {f: [] for f in fields}
    for it in top10:
        r = ratios.get(it["ticker"], {})
        for f in fields:
            v = r.get(f)
            if v is not None:
                vals[f].append(v)
    avgs = {}
    for f, lst in vals.items():
        if lst:
            s = sorted(lst)
            n = len(s)
            avgs[f] = round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 2)
    return avgs


# ─── 표지 페이지 ──────────────────────────────────────────────────────────────

def _draw_cover(pdf: PdfPages, rankings: dict, themes: dict) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(NAVY)
    ax.axis("off")

    total_stocks = sum(len(v.get("top10", [])) for v in rankings.values())
    highlighted  = sum(
        1 for krx in rankings
        if themes.get(krx, {}).get("highlight")
    )
    today = date.today().strftime("%Y년 %m월 %d일")

    ax.text(0.5, 0.80, "Korea Stock Agent", ha="center", color="white",
            fontsize=28, fontweight="bold")
    ax.text(0.5, 0.72, "종목 스코어카드", ha="center", color=GOLD,
            fontsize=22, fontweight="bold")
    ax.text(0.5, 0.64, today, ha="center", color="white", fontsize=14)

    ax.plot([0.15, 0.85], [0.57, 0.57], color=GOLD, lw=1.5)

    stats = [
        ("모니터링 섹터", f"{len(rankings)}개"),
        ("주목 섹터",     f"{highlighted}개"),
        ("추적 종목",     f"{total_stocks}개"),
    ]
    for i, (label, val) in enumerate(stats):
        x = 0.22 + i * 0.28
        ax.text(x, 0.49, val,   ha="center", color=GOLD,    fontsize=18, fontweight="bold")
        ax.text(x, 0.44, label, ha="center", color="#cbd5e1", fontsize=10)

    ax.plot([0.15, 0.85], [0.40, 0.40], color=GOLD, lw=0.8, alpha=0.5)
    ax.text(0.5, 0.34, "[ 주목 섹터 ]", ha="center", color=GOLD, fontsize=12, fontweight="bold")

    hl_names = [krx for krx in rankings if themes.get(krx, {}).get("highlight")]
    for i, name in enumerate(hl_names[:8]):
        col = i % 4
        row = i // 4
        ax.text(0.12 + col * 0.20, 0.28 - row * 0.07, f"• {name}",
                ha="left", color="#e2e8f0", fontsize=9)

    ax.text(0.5, 0.05, "본 문서는 자동 생성된 투자 참고 자료입니다.",
            ha="center", color="#64748b", fontsize=8)

    pdf.savefig(fig, bbox_inches="tight", facecolor=NAVY)
    plt.close(fig)


# ─── 섹터 페이지 ──────────────────────────────────────────────────────────────

def _draw_sector(pdf: PdfPages, sector: str, v: dict, ratios: dict,
                 is_highlighted: bool) -> None:
    top10 = v.get("top10", [])
    if not top10:
        return

    avg = _sector_avg(top10, ratios)

    fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape
    fig.patch.set_facecolor("white")

    # ── 섹터 헤더 바 ─────────────────────────────────────────────────────────
    header_ax = fig.add_axes([0.02, 0.88, 0.96, 0.10])
    header_ax.set_facecolor(NAVY)
    header_ax.axis("off")
    hl_tag = "  [주목]" if is_highlighted else ""
    header_ax.text(0.02, 0.5, f"{sector}{hl_tag}",
                   color="white", fontsize=16, fontweight="bold", va="center")
    header_ax.text(0.98, 0.5, f"기준일: {v.get('as_of', '')}",
                   color="#cbd5e1", fontsize=9, va="center", ha="right")

    # ── 섹터 평균 요약 바 ─────────────────────────────────────────────────────
    avg_ax = fig.add_axes([0.02, 0.78, 0.96, 0.09])
    avg_ax.set_facecolor(LGRAY)
    avg_ax.axis("off")
    avg_items = [
        ("PER 중앙값",   _f(avg.get("per"),            1)),
        ("PBR 중앙값",   _f(avg.get("pbr"),            2)),
        ("ROE 평균",     _f(avg.get("roe"),            1, "%")),
        ("부채비율 평균", _f(avg.get("debt_ratio"),     0, "%")),
        ("매출성장 평균", _f(avg.get("revenue_growth"), 1, "%")),
    ]
    avg_ax.text(0.01, 0.55, "섹터 평균", color=DGRAY, fontsize=8, fontweight="bold", va="center")
    for i, (label, val) in enumerate(avg_items):
        x = 0.10 + i * 0.18
        avg_ax.text(x, 0.75, val,   color=NAVY, fontsize=11, fontweight="bold", va="center")
        avg_ax.text(x, 0.25, label, color=DGRAY, fontsize=7,  va="center")

    # ── 메인 테이블 ───────────────────────────────────────────────────────────
    table_ax = fig.add_axes([0.02, 0.02, 0.96, 0.74])
    table_ax.axis("off")

    col_labels = ["순위", "종목명", "시총(억)", "등락률(%)", "PER", "PBR",
                  "ROE(%)", "EPS(원)", "부채비율(%)", "매출성장(%)"]
    col_widths = [0.05, 0.14, 0.10, 0.09, 0.07, 0.07, 0.07, 0.10, 0.10, 0.10]

    rows: list[list[str]] = []
    for item in top10:
        ticker = item["ticker"]
        r      = ratios.get(ticker, {})
        chg    = item.get("change_rate", 0) or 0
        rows.append([
            str(item["rank"]),
            item.get("name", ticker),
            _f(item.get("market_cap_억"), 0),
            f"{chg:+.2f}",
            _f(r.get("per"),            1),
            _f(r.get("pbr"),            2),
            _f(r.get("roe"),            1),
            _f(r.get("eps"),            0),
            _f(r.get("debt_ratio"),     0),
            _f(r.get("revenue_growth"), 1),
        ])

    tbl = table_ax.table(
        cellText=rows,
        colLabels=col_labels,
        colWidths=col_widths,
        loc="upper center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.9)

    # 헤더 스타일
    for j in range(len(col_labels)):
        cell = tbl[0, j]
        cell.set_facecolor(NAVY)
        cell.set_text_props(color="white", fontweight="bold")
        cell.set_linewidth(0)

    # 데이터 행 스타일 (교대 배경 + 조건부 색상)
    per_col, roe_col, chg_col = 4, 6, 3
    for i, row_data in enumerate(rows, start=1):
        bg = LGRAY if i % 2 == 0 else "white"
        for j in range(len(col_labels)):
            cell = tbl[i, j]
            cell.set_facecolor(bg)
            cell.set_linewidth(0.3)
            cell.set_edgecolor("#e5e7eb")

            # 등락률 색상
            if j == chg_col:
                try:
                    val = float(row_data[j])
                    cell.get_text().set_color(GREEN if val > 0 else (RED if val < 0 else "black"))
                except ValueError:
                    pass
            # PER 색상 (낮을수록 green)
            elif j == per_col:
                cell.get_text().set_color(_color_per(row_data[j], avg.get("per")))
            # ROE 색상 (높을수록 green)
            elif j == roe_col:
                cell.get_text().set_color(_color_roe(row_data[j], avg.get("roe")))

    # 종목명은 왼쪽 정렬
    for i in range(len(rows) + 1):
        tbl[i, 1].set_text_props(ha="left")

    pdf.savefig(fig, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ─── 공개 API ─────────────────────────────────────────────────────────────────

def generate_scorecard(
    rankings_data: dict,
    ratios_data:   dict,
    themes_data:   dict,
) -> bytes:
    """섹터 스코어카드 PDF를 생성하여 bytes로 반환."""
    buf = io.BytesIO()

    with PdfPages(buf) as pdf:
        _draw_cover(pdf, rankings_data, themes_data)

        # 주목 섹터 먼저
        for sector, v in sorted(rankings_data.items()):
            is_hl = themes_data.get(sector, {}).get("highlight", False)
            if is_hl:
                _draw_sector(pdf, sector, v, ratios_data, True)

        # 나머지 섹터
        for sector, v in sorted(rankings_data.items()):
            is_hl = themes_data.get(sector, {}).get("highlight", False)
            if not is_hl:
                _draw_sector(pdf, sector, v, ratios_data, False)

        pdf.infodict().update({
            "Title":   "Korea Stock Agent — 종목 스코어카드",
            "Author":  "Korea Stock Agent",
            "Subject": f"섹터별 시총 Top10 재무비율 ({date.today()})",
        })

    buf.seek(0)
    return buf.read()
