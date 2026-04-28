"""
GitHub API 기반 파일 스토리지.
Streamlit Cloud에서 trade_notes.json을 GitHub 레포에 직접 커밋해 영속화.
secrets에 GITHUB_TOKEN/OWNER/REPO가 없으면 로컬 파일 폴백.
"""
from __future__ import annotations

import base64
import json
import os

import requests


def _cfg() -> tuple[str, str, str, str] | None:
    """(token, owner, repo, branch) — 설정 없으면 None."""
    try:
        import streamlit as st
        token  = st.secrets.get("GITHUB_TOKEN", "")
        owner  = st.secrets.get("GITHUB_OWNER", "")
        repo   = st.secrets.get("GITHUB_REPO",  "")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:
        token  = os.getenv("GITHUB_TOKEN", "")
        owner  = os.getenv("GITHUB_OWNER", "")
        repo   = os.getenv("GITHUB_REPO",  "")
        branch = os.getenv("GITHUB_BRANCH", "main")
    return (token, owner, repo, branch) if all([token, owner, repo]) else None


def _headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}


def is_available() -> bool:
    return _cfg() is not None


def load(path: str) -> dict | None:
    """GitHub에서 JSON 로드. 파일 없으면 {}, API 오류면 None."""
    cfg = _cfg()
    if not cfg:
        return None
    token, owner, repo, branch = cfg
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    try:
        resp = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=10)
    except Exception:
        return None
    if resp.status_code == 404:
        return {}
    if not resp.ok:
        return None
    raw = base64.b64decode(resp.json()["content"]).decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def save(path: str, data: dict, message: str = "트레이드 노트 업데이트") -> bool:
    """GitHub에 JSON 저장. 성공 여부 반환."""
    cfg = _cfg()
    if not cfg:
        return False
    token, owner, repo, branch = cfg
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    # 현재 SHA 조회 (파일 수정에 필요)
    try:
        get_resp = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=10)
        sha = get_resp.json().get("sha") if get_resp.ok else None
    except Exception:
        sha = None

    content_b64 = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")

    body: dict = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        body["sha"] = sha

    try:
        put_resp = requests.put(url, headers=_headers(token), json=body, timeout=15)
        return put_resp.status_code in (200, 201)
    except Exception:
        return False
