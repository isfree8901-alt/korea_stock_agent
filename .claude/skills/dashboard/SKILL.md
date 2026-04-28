# Skill: dashboard

## 목적
output/ 디렉토리의 JSON 파일들을 로드하여 Streamlit 웹 대시보드로 시각화한다.

## 스크립트

| 스크립트 | 역할 |
|---------|------|
| `scripts/app.py` | Streamlit 대시보드 메인 |

## 실행 방법
```bash
# 프로젝트 루트에서 실행
streamlit run .claude/skills/dashboard/scripts/app.py
```

## 대시보드 구성 (7개 섹션)

| 섹션 | 데이터 소스 | 내용 |
|-----|-----------|-----|
| 파이프라인 상태 | 파일 존재 여부 | STEP별 완료/미완료 배지 |
| 섹터 선정 결과 | step3_sector_selection.json | 선정 근거 + 뉴스 링크 |
| 포트폴리오 시그널 | step4_portfolio_signals.json | BUY/SELL/HOLD 컬러 테이블 |
| 재무 분석 | step5_financial_analysis.json | 지표 테이블 + ROE/PER/산점도 차트 |
| 백테스트 결과 | step6_backtest_result.json | 4개 메트릭 + 누적수익률 차트 |
| 공시 경고 | step4 + step5 | 경고 종목 목록 |
| 파이프라인 로그 | pipeline_warn/error.log | 로그 텍스트 |

## 실패 처리
- JSON 파일 누락 또는 파싱 오류 → 해당 섹션에 `st.error()` 표시
- 나머지 섹션은 정상 렌더링 유지 (`load_json_safe()` 패턴)
