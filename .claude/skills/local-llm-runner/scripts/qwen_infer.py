"""
Ollama(로컬 LLM) 추론 래퍼.
--task sector_selection | financial_eval
--input  입력 JSON 파일 경로
--output 출력 JSON 파일 경로
--retry  교정 서브프롬프트 포함 여부
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
REFS_DIR = Path(__file__).resolve().parent.parent / "references"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")
TIMEOUT = 300


def load_prompt_template(task: str, retry: bool = False) -> str:
    filename = {
        "sector_selection": "sector_selection_prompt.md",
        "financial_eval": "financial_eval_prompt.md",
    }[task]
    template = (REFS_DIR / filename).read_text(encoding="utf-8")
    if retry:
        correction = (
            "\n\n## 교정 지시\n"
            "이전 출력에 스키마 오류가 있었습니다: {error_list}\n"
            "위에 명시된 JSON 스키마를 정확히 따라 JSON 객체만 반환하세요. "
            "JSON 외 다른 텍스트는 절대 포함하지 마세요."
        )
        template += correction
    return template


def build_prompt(template: str, input_data: dict,
                 error_list: list | None = None) -> str:
    data_str = json.dumps(input_data, ensure_ascii=False, indent=2)
    prompt = template.replace("{sector_summaries_json}", data_str)
    prompt = prompt.replace("{financial_indicators_json}", data_str)
    if error_list is not None:
        prompt = prompt.replace("{error_list}", str(error_list))
    return prompt


def call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Ollama 서버에 연결할 수 없습니다 ({OLLAMA_HOST})", file=sys.stderr)
        sys.exit(2)
    except requests.exceptions.Timeout:
        print(f"ERROR: Ollama 응답 타임아웃 ({TIMEOUT}초)", file=sys.stderr)
        sys.exit(2)


def extract_json(raw: str) -> dict:
    # 직접 파싱 시도
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # 코드 블록 내 JSON 탐색 (```json ... ```)
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # 첫 번째 { ... } 블록 탐색
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"LLM 응답에서 유효한 JSON을 찾지 못했습니다.\n응답 앞부분: {raw[:300]}")


def run_inference(task: str, input_path: str, output_path: str,
                  retry: bool = False) -> None:
    input_data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    template = load_prompt_template(task, retry)
    prompt = build_prompt(template, input_data)

    print(f"[qwen_infer] task={task} model={OLLAMA_MODEL} retry={retry}")
    raw = call_ollama(prompt)

    try:
        result = extract_json(raw)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    # financial_eval 결과의 티커 키를 6자리 zero-padding으로 정규화
    if task == "financial_eval" and isinstance(result, dict):
        result = {
            (k.zfill(6) if k.isdigit() else k): v
            for k, v in result.items()
        }

    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[qwen_infer] 출력 저장 → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Ollama LLM 추론 래퍼")
    parser.add_argument("--task", required=True,
                        choices=["sector_selection", "financial_eval"])
    parser.add_argument("--input", required=True, help="입력 JSON 파일 경로")
    parser.add_argument("--output", required=True, help="출력 JSON 파일 경로")
    parser.add_argument("--retry", action="store_true", help="교정 프롬프트 사용")
    args = parser.parse_args()

    run_inference(args.task, args.input, args.output, args.retry)


if __name__ == "__main__":
    main()
