"""KOSIS 공유서비스 응답 파서.

🔴 **`jsonVD=Y` 가 없으면 JSON 이 아니다.** `format=json` 만 주면 키에 따옴표가 없는
   **자바스크립트 객체 리터럴**이 온다:

       format=json          → [{LIST_NM:"인구총조사",LIST_ID:"A_4"}]     ← json.loads 실패
       format=json&jsonVD=Y → [{"LIST_NM":"인구총조사","LIST_ID":"A_4"}]  ← 정상

   개발가이드(172쪽)의 **입력 변수 표에는 `jsonVD` 가 없다.** JSP 예제 소스 안에만
   나온다. 표만 보고 만들면 파서가 첫 응답에서 죽는다. `client` 가 항상 붙인다.

🔴 **모든 실패가 HTTP 200 이다.** 오류는 `{"err":"NN","errMsg":"…"}` 객체로 온다.
   성공은 **배열**, 실패는 **객체** — 이 구분이 가장 확실한 판별 기준이다.

⚠️ `Content-Type` 은 성공·실패 모두 `text/html;charset=UTF-8` 이다. 믿지 말 것.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import ERROR_CODES, scrub


class ParseError(RuntimeError):
    """응답이 기대한 봉투가 아닐 때."""


class ApiError(ParseError):
    """KOSIS 가 오류 봉투를 돌려줬을 때."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = str(code)
        self.retryable = retryable
        super().__init__(message)


class NoData(ApiError):
    """err 30 — 조회 결과 없음. **오류가 아니라 사실**인 경우가 많다."""

    def __init__(self, message: str):
        super().__init__("30", message, retryable=False)


class TooManyCells(ApiError):
    """err 31 — 조회결과 초과(4만 셀 제한).

    🔴 이것은 **쪼개면 해결된다**. 실측: 같은 표에서 newEstPrdCnt=12 는 10,590행으로
       정상, 60 은 err 31. 클라이언트가 시점을 나눠 다시 부른다.
    """

    def __init__(self, message: str):
        super().__init__("31", message, retryable=False)


class RateLimited(ApiError):
    """err 40 — 분당 호출 제한(200건). 기다리면 풀린다."""

    def __init__(self, message: str):
        super().__init__("40", message, retryable=True)


# 재시도가 의미 있는 코드. 인증·필수값 오류는 재시도해도 같은 답이다.
_RETRYABLE = {"40", "50"}
_SPECIAL = {"30": NoData, "31": TooManyCells, "40": RateLimited}

# 🔴 자바스크립트 객체 리터럴에서 **키에만** 따옴표를 붙이는 구조 — 값은 건드리지 않는다.
#    (jsonVD 를 빠뜨린 응답을 진단할 때만 쓴다. 정상 경로는 이 길로 오지 않는다.)
_BARE_KEY = re.compile(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:')


def looks_like_js_literal(text: str) -> bool:
    """`jsonVD` 를 빠뜨린 응답인가 — 진단 메시지를 정확히 내기 위해."""
    t = text.lstrip()
    return bool(t[:1] in "[{" and _BARE_KEY.search(t[:400]))


def parse(body: bytes | str, *, what: str) -> Any:
    """응답 → 파이썬 객체. 오류 봉투는 예외로 올린다.

    ⚠️ 빈 응답을 '결과 0건'으로 통과시키지 않는다 — `method` 를 빠뜨리면 KOSIS 는
       **빈 본문**을 준다(실측). 그것을 0건으로 읽으면 조용한 사고가 된다.
    """
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    if not text.strip():
        raise ParseError(
            f"{what}: 응답이 **비어 있습니다**. KOSIS 는 `method` 를 빠뜨리면 오류 대신 "
            f"빈 본문을 돌려줍니다(실측). '결과 0건'이 아닙니다.")

    stripped = text.lstrip()
    if stripped.startswith("<"):
        # 대용량 서비스는 XML 로 오류를 준다: <error><err>11</err>…
        m = re.search(r"<err>(\d+)</err>", text)
        if m:
            code = m.group(1)
            raise _make(code, f"{what}: {ERROR_CODES.get(code, '알 수 없는 오류')} (코드 {code})")
        raise ParseError(
            f"{what}: XML/HTML 이 왔습니다(JSON 이 아닙니다) — 앞부분: {scrub(text[:160])!r}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if looks_like_js_literal(text):
            raise ParseError(
                f"{what}: 🔴 **`jsonVD=Y` 가 빠졌습니다.** `format=json` 만 주면 키에 "
                f"따옴표가 없는 자바스크립트 객체 리터럴이 옵니다(개발가이드의 입력 변수 "
                f"표에는 이 파라미터가 없고 JSP 예제 안에만 있습니다). "
                f"앞부분: {scrub(text[:120])!r}") from None
        raise ParseError(f"{what}: JSON 파싱 실패 — 앞부분: {scrub(text[:160])!r}") from None

    # 🔴 실패는 객체, 성공은 배열이다.
    if isinstance(data, dict) and "err" in data:
        code = str(data.get("err"))
        msg = str(data.get("errMsg") or ERROR_CODES.get(code, ""))
        raise _make(code, f"{what}: {msg} (코드 {code})")
    return data


def _make(code: str, message: str) -> ApiError:
    cls = _SPECIAL.get(code)
    if cls:
        return cls(scrub(message))
    return ApiError(code, scrub(message), retryable=code in _RETRYABLE)


def parse_rows(body: bytes | str, *, what: str) -> list[dict]:
    """배열 응답 → 레코드 목록.

    ⚠️ 배열이 아니면 스키마가 바뀐 것이다 — 빈 목록으로 통과시키지 않는다.
    """
    data = parse(body, what=what)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        # 단건을 객체로 주는 서비스가 있을 수 있다 — 감싸서 돌려준다.
        return [data]
    raise ParseError(f"{what}: 배열이 아닌 응답입니다({type(data).__name__}).")
