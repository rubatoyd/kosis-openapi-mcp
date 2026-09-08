"""라이브 탐침 — KOSIS 공유서비스의 실제 동작을 확정한다.

개발가이드(ref/KOSIS 공유서비스 개발가이드.pdf, 172쪽)는 **입력·출력 변수 표**까지다.
오류 봉투·상한·빈 결과의 모양은 적혀 있지 않거나 실제와 다를 수 있다.
자매 저장소들이 반복해서 물린 것이 그 자리다 — 그래서 여기서 잰다.

    python scripts/probe_api.py auth      # 인증키 형태·오류 봉투
    python scripts/probe_api.py shapes    # 서비스별 응답 모양
    python scripts/probe_api.py limits    # 결과 건수 상한·페이징
    python scripts/probe_api.py all

⚠️ 인증키는 절대 출력하지 않는다(응답 에코 포함 — `scrub` 을 태운다).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosis_mcp.config import (  # noqa: E402
    ENDPOINTS, get_api_key, scrub, use_os_trust)

THROTTLE = 0.5
_s = requests.Session()
_s.headers["User-Agent"] = "kosis-openapi-mcp-probe (+github.com/rubatoyd/kosis-openapi-mcp)"


class ProbeError(RuntimeError):
    """탐침 실패 — 🔴 **요청 URL 을 절대 싣지 않는다.**"""


def call(url: str, **params):
    # 🔴 `jsonVD=Y` 가 없으면 **JSON 이 아니라 자바스크립트 객체 리터럴**이 온다
    #    (키에 따옴표가 없어 json.loads 가 거부한다). 개발가이드의 입력 변수 표에는
    #    이 파라미터가 **없고**, JSP 소스 안에만 있다.
    params.setdefault("jsonVD", "Y")
    # 🔴 requests 예외는 **인증키가 든 전체 URL** 을 메시지에 박는다. 여기서 잡지 않으면
    #    망 오류 한 번에 키가 콘솔·CI 로그·스크린샷으로 나간다(2026-09-08 실제 발생).
    #    client._get 이 같은 이유로 raise_for_status 를 쓰지 않는다 — 탐침도 같아야 한다.
    try:
        r = _s.get(url, params=params, timeout=40)
    except requests.RequestException as e:
        raise ProbeError(f"요청 실패: {type(e).__name__}") from None
    time.sleep(THROTTLE)
    return r


def _peek(r: requests.Response, n: int = 260) -> dict:
    return {"http": r.status_code, "bytes": len(r.content),
            "ctype": r.headers.get("Content-Type", ""),
            "head": scrub(r.text[:n])}


def auth() -> dict:
    """인증키 형태와 오류 봉투. 🔴 KOSIS 키는 base64 처럼 보이지만 **그대로** 쓴다."""
    key = get_api_key()
    url = ENDPOINTS["list"]
    base = dict(method="getList", vwCd="MT_ZTITLE", parentListId="A", format="json")
    out = {}
    out["정상"] = _peek(call(url, apiKey=key, **base))
    out["키_없음"] = _peek(call(url, **base))
    out["키_틀림"] = _peek(call(url, apiKey="deadbeef", **base))
    # 🔴 base64 로 보이는 키를 '풀어서' 보내면 어떻게 되는가 (흔한 오해)
    try:
        import base64
        decoded = base64.b64decode(key).decode("utf-8", "replace")
        out["키_디코드"] = _peek(call(url, apiKey=decoded, **base))
    except Exception as e:  # noqa: BLE001
        out["키_디코드"] = {"skip": type(e).__name__}
    out["필수누락_vwCd"] = _peek(call(url, apiKey=key, method="getList",
                                    parentListId="A", format="json"))
    out["필수누락_parentListId"] = _peek(call(url, apiKey=key, method="getList",
                                           vwCd="MT_ZTITLE", format="json"))
    out["없는_vwCd"] = _peek(call(url, apiKey=key, method="getList",
                                vwCd="NOPE", parentListId="A", format="json"))
    out["없는_method"] = _peek(call(url, apiKey=key, method="nope",
                                  vwCd="MT_ZTITLE", parentListId="A", format="json"))
    return out


def shapes() -> dict:
    """서비스별 응답 모양 — 배열인가 객체인가, 키 이름은 무엇인가."""
    key = get_api_key()
    out = {}

    r = call(ENDPOINTS["list"], apiKey=key, method="getList",
             vwCd="MT_ZTITLE", parentListId="A", format="json")
    out["통계목록"] = _shape(r)

    # 통계자료 — 통계표선택 방법(orgId/tblId). 인구총조사 표 하나를 자로 쓴다.
    r = call(ENDPOINTS["data"], apiKey=key, method="getList", format="json",
             orgId="101", tblId="DT_1B040A3", objL1="ALL", itmId="ALL",
             prdSe="Y", newEstPrdCnt="2")
    out["통계자료"] = _shape(r)

    r = call(ENDPOINTS["expl"], apiKey=key, method="getList", format="json",
             orgId="101", tblId="DT_1B040A3")
    out["통계설명"] = _shape(r)

    r = call(ENDPOINTS["search"], apiKey=key, method="getList", format="json",
             searchNm="인구")
    out["통합검색"] = _shape(r)

    r = call(ENDPOINTS["meta"], apiKey=key, method="getMeta", type="TBL",
             format="json", orgId="101", tblId="DT_1B040A3")
    out["메타_TBL"] = _shape(r)
    return out


def _shape(r: requests.Response) -> dict:
    e = _peek(r, 200)
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        e["json"] = False
        return e
    e["json"] = True
    if isinstance(j, list):
        e["kind"] = "array"
        e["n"] = len(j)
        e["keys"] = sorted(j[0].keys()) if j and isinstance(j[0], dict) else None
    elif isinstance(j, dict):
        e["kind"] = "object"
        e["keys"] = sorted(j.keys())
        for k in ("err", "errMsg"):
            if k in j:
                e[k] = j[k]
    return e


def limits() -> dict:
    """결과 건수 상한과 페이징 — 문서에 없다."""
    key = get_api_key()
    out = {}
    # 통계자료: 큰 표를 전 기간으로 부르면 몇 건까지 오는가
    for cnt in ("5", "50", "200"):
        r = call(ENDPOINTS["data"], apiKey=key, method="getList", format="json",
                 orgId="101", tblId="DT_1B040A3", objL1="ALL", itmId="ALL",
                 prdSe="Y", newEstPrdCnt=cnt)
        try:
            j = r.json()
            n = len(j) if isinstance(j, list) else None
            err = j.get("err") if isinstance(j, dict) else None
        except Exception:  # noqa: BLE001
            n, err = None, "parse"
        out[f"newEstPrdCnt={cnt}"] = {"rows": n, "err": err, "bytes": len(r.content)}
    # 페이징 파라미터가 있는가 (문서에 없다 — 있는지 본다)
    r = call(ENDPOINTS["list"], apiKey=key, method="getList",
             vwCd="MT_ZTITLE", parentListId="A", format="json")
    try:
        out["통계목록_A_건수"] = len(r.json())
    except Exception:  # noqa: BLE001
        out["통계목록_A_건수"] = None
    return out


COMMANDS = {"auth": auth, "shapes": shapes, "limits": limits}


def main(argv: list[str]) -> int:
    use_os_trust()
    if not get_api_key():
        raise SystemExit("KOSIS_API_KEY 미설정 — .env 또는 환경변수로 주세요.")
    what = argv[0] if argv else "all"
    names = list(COMMANDS) if what == "all" else [what]
    if any(n not in COMMANDS for n in names):
        print(f"사용법: probe_api.py [{'|'.join(COMMANDS)}|all]", file=sys.stderr)
        return 2
    try:
        result = {n: COMMANDS[n]() for n in names}
    except ProbeError as e:
        # 메시지에 URL 이 없다는 것이 요점이다 — traceback 도 띄우지 않는다.
        print(f"탐침 실패: {scrub(str(e))}", file=sys.stderr)
        return 1
    Path("probe-out").mkdir(exist_ok=True)
    Path(f"probe-out/probe_{what}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
