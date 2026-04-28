#!/bin/bash
# 섹터 리밸런싱 파이프라인 (LLM-Free)
# 사용법:
#   ./run_pipeline.sh        → STEP 1부터 전체 실행
#   ./run_pipeline.sh 2      → STEP 2부터 실행 (데이터 수집 건너뜀)
#   ./run_pipeline.sh 1 2    → STEP 1~2만 실행

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# .env 로드 (공백 있는 key=value 안전 처리)
set +e
while IFS='=' read -r key val; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    key="${key// /}"; val="${val// /}"
    export "$key=$val"
done < .env
set -e

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; }

FROM_STEP=${1:-1}
TO_STEP=${2:-4}

echo "========================================"
echo "  Korea Stock Agent — 리밸런싱 파이프라인"
echo "  STEP ${FROM_STEP} ~ ${TO_STEP}"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 전제 조건: DART API KEY
if [ "$FROM_STEP" -le 1 ]; then
    if [ -z "$DART_API_KEY" ]; then
        fail "DART_API_KEY가 .env에 없습니다."; exit 1
    fi
    ok "DART_API_KEY 확인"
fi

# ── STEP 1: 데이터 수집 ──────────────────────────────────────────────────────
if [ "$FROM_STEP" -le 1 ] && [ "$TO_STEP" -ge 1 ]; then
    echo ""
    echo "▶ STEP 1 — 원천 데이터 수집"

    echo "  [1/3] KRX 시장 데이터 (시가총액·주식수·등락률)..."
    if python3 .claude/skills/data-collector/scripts/fetch_krx.py; then
        ok "fetch_krx 완료"
    else
        fail "fetch_krx 실패 — 파이프라인 중단 (CRITICAL)"; exit 1
    fi

    echo "  [2/3] DART 재무 로우 데이터 (순이익·자본·매출·EPS 원천값)..."
    python3 .claude/skills/data-collector/scripts/fetch_dart.py \
        || warn "fetch_dart 부분 실패 (NON-CRITICAL, 계속 진행)"
    ok "fetch_dart 완료"

    echo "  [3/3] 뉴스 수집..."
    python3 .claude/skills/data-collector/scripts/fetch_news.py \
        || warn "fetch_news 부분 실패 (NON-CRITICAL)"
    ok "fetch_news 완료"
fi

# ── STEP 2: 분석 ─────────────────────────────────────────────────────────────
if [ "$FROM_STEP" -le 2 ] && [ "$TO_STEP" -ge 2 ]; then
    echo ""
    echo "▶ STEP 2 — 분석 (순위 추적 · 재무비율 · 뉴스 테마)"

    echo "  [1/6] 뉴스 중복 제거..."
    python3 .claude/skills/news-preprocessor/scripts/deduplicate.py

    echo "  [2/6] 핵심 문장 추출..."
    python3 .claude/skills/news-preprocessor/scripts/extract_sentences.py

    echo "  [3/6] 섹터 감성 점수..."
    python3 .claude/skills/news-preprocessor/scripts/score_sentiment.py

    echo "  [4/6] 섹터 종합 스코어카드 (감성+모멘텀+재무)..."
    python3 .claude/skills/news-preprocessor/scripts/build_sector_scorecard.py

    echo "  [5/6] 뉴스 테마 추출 → 6개월 주목 섹터..."
    python3 .claude/skills/news-preprocessor/scripts/extract_themes.py

    echo "  [6/6] 섹터별 시총 Top10 순위 추적..."
    python3 .claude/skills/portfolio-builder/scripts/track_sector_rankings.py

    ok "분석 완료"
fi

# ── STEP 3: 재무비율 계산 ────────────────────────────────────────────────────
if [ "$FROM_STEP" -le 3 ] && [ "$TO_STEP" -ge 3 ]; then
    echo ""
    echo "▶ STEP 3 — 재무비율 계산 (PER·ROE·PBR·EPS 성장률)"
    python3 .claude/skills/financial-analyzer/scripts/calc_ratios.py
    ok "재무비율 계산 완료"
fi

# ── STEP 3.5: 트레이드 노트 고점가 자동 갱신 ─────────────────────────────────
if [ "$FROM_STEP" -le 3 ] && [ "$TO_STEP" -ge 3 ]; then
    python3 -c "
from pathlib import Path; import json
notes_path = Path('data/trade_notes.json')
mkt_path   = Path('output/step1_market_data.json')
if notes_path.exists() and mkt_path.exists():
    notes = json.loads(notes_path.read_text(encoding='utf-8'))
    mkt   = json.loads(mkt_path.read_text(encoding='utf-8'))
    changed = 0
    for nm, n in notes.items():
        if n.get('status') != '보유중': continue
        cur = mkt.get(n.get('ticker',''), {}).get('close')
        if cur and cur > n.get('peak_price', n.get('buy_price', 0)):
            n['peak_price'] = cur; changed += 1
    if changed:
        notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'[peak] 고점가 갱신 {changed}건')
" || true
fi

# ── STEP 4: Telegram 알림 ────────────────────────────────────────────────────
if [ "$FROM_STEP" -le 4 ] && [ "$TO_STEP" -ge 4 ]; then
    echo ""
    echo "▶ STEP 4 — Telegram 알림 발송"
    python3 .claude/skills/notifier/scripts/notify.py \
        || warn "알림 발송 실패 (NON-CRITICAL, 계속 진행)"
    ok "알림 단계 완료"
fi

# ── 완료 ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
ok "파이프라인 완료! (STEP ${FROM_STEP}~${TO_STEP})"
echo "  종료 시각: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# ── GitHub 자동 푸시 (Streamlit Cloud 갱신) ──────────────────────────────────
if git rev-parse --git-dir > /dev/null 2>&1; then
    echo ""
    echo "▶ GitHub 푸시 — Streamlit Cloud 데이터 갱신"
    git add output/*.json
    if git diff --cached --quiet; then
        warn "output 변경 없음 — 푸시 생략"
    else
        git commit -m "데이터 업데이트 $(date '+%Y-%m-%d %H:%M:%S')" \
            && git push \
            && ok "GitHub 푸시 완료" \
            || warn "GitHub 푸시 실패 (NON-CRITICAL)"
    fi
fi

echo ""
echo "대시보드 실행:"
echo "  streamlit run .claude/skills/dashboard/scripts/app.py"
