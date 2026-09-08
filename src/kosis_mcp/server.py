"""KOSIS 공유서비스 MCP 서버 (FastMCP).

⚠️ `mcp.server.fastmcp` 를 **조건부 import 하지 않는다.** mcp 2.0 은 이 모듈을 제거했으므로
   폴백을 두면 반쯤 동작하는 서버가 조용히 뜬다. pyproject 의 `mcp>=1.2.0,<2` 상한이
   유일한 방어선이고, 여기서는 실패를 시끄럽게 내는 편이 옳다.
"""
from __future__ import annotations

import argparse
import functools
import os
import sys

from mcp.server.fastmcp import FastMCP

from .client import KosisClient, KosisError
from .config import (
    ERROR_CODES,
    MAX_CALLS_PER_TOOL_CALL,
    META_TYPES,
    PERIODS,
    VIEW_CODES,
    get_api_key,
    scrub,
)
from .exporters import export

mcp = FastMCP("kosis")


def _safe(fn):
    """도구는 **항상 JSON 직렬화 가능한 dict** 를 반환 — 예외 누수 금지.

    ⚠️ 메시지에 **요청 URL 을 싣지 않는다** — 인증키가 화면·스크린샷으로 나간다.
       `scrub()` 이 이중 방어다.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": scrub(f"{type(e).__name__}: {e}")}
    return wrapper


_READ = {"readOnlyHint": True, "openWorldHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}


def _obj_levels(*values: str) -> dict[str, str]:
    """obj_l2~obj_l8 → {'objL2': …} — **빈 값은 싣지 않는다.**

    🔴 빈 축을 보내면 err 21 이다(축이 하나라도 남으면 개수가 어긋난다). 비워 둔 축은
       클라이언트가 err 20 `(objL)` 을 보고 필요한 만큼만 'ALL' 로 채운다.
    """
    return {f"objL{i}": v.strip() for i, v in enumerate(values, start=2) if v.strip()}


_NO_KEY = {
    "error": "KOSIS_API_KEY 미설정 — KOSIS 공유서비스는 인증키가 필요합니다.",
    "hint": "kosis.kr 회원가입 후 공유서비스 활용신청(자동 승인)에서 인증키를 받아 "
            "환경변수 KOSIS_API_KEY 로 설정하세요. 🔴 발급된 값을 **그대로** 쓰세요 — "
            "base64 처럼 보여도 디코드하면 인증이 깨집니다.",
}


@mcp.tool(annotations=_READ)
@_safe
def kosis_status() -> dict:
    """연결 점검 — 인증키 보유 여부 + KOSIS 실제 왕복 1회."""
    if not get_api_key():
        return {**_NO_KEY, "ok": False, "has_api_key": False}
    return KosisClient().status()


@mcp.tool(annotations=_READ)
@_safe
def kosis_guide() -> dict:
    """이 API 를 쓸 때 알아야 할 것 — 서비스뷰·주기·메타 종류·한계·함정."""
    return {
        "인용_단위": "통계표 하나가 레코드 하나다. KOSIS 는 조사(STAT_NM)와 표(TBL_NM) "
                     "두 층을 다 주므로 레코드에 둘 다 실린다 — 인용하는 쪽이 고르면 된다. "
                     "수치 한 칸(관측치)은 문헌이 아니라 위치다.",
        "서비스뷰": VIEW_CODES,
        "수록주기": PERIODS,
        "메타_종류": META_TYPES,
        "오류코드": ERROR_CODES,
        "한계": {
            "요청당_셀": "4만 셀 — 넘으면 err 31. kosis_data 가 기간을 자동으로 쪼갠다.",
            "분당_호출": "200건 — 넘으면 err 40(기다리면 풀린다).",
            "도구_호출당_요청_상한": MAX_CALLS_PER_TOOL_CALL,
            "페이징": "통합검색·통계목록에는 페이징 파라미터가 없다 — 서버가 준 만큼이 전부다.",
        },
        "분류축": {
            "규칙": "요청의 objL 개수가 표의 분류축 개수와 **정확히** 맞아야 한다 — "
                    "모자라면 err 20 '(objL)', 넘치면 err 21. 그래서 '전부 보내기'는 통하지 않는다.",
            "축_개수를_아는_법": "없다. 축을 알려 주는 메타 서비스가 없다"
                                 "(NCD 는 분류가 아니라 신규수록 시점이고 OBJ·CLS 는 err 30). "
                                 "kosis_data 가 하나씩 늘려 가며 맞추고 meta.obj_levels 로 알린다.",
            "직접_지정": "obj_l2~obj_l8 에 코드를 주면 그 지점에서 출발한다 — "
                         "err 31 에 걸릴 때 축을 좁히는 자리다.",
        },
        "함정": [
            "🔴 `jsonVD=Y` 가 없으면 JSON 이 아니라 자바스크립트 객체 리터럴이 온다"
            "(키에 따옴표가 없다). 개발가이드 입력 변수 표에 없는 파라미터다.",
            "🔴 통계자료를 orgId/tblId 로 부를 때는 `/openapi/Param/"
            "statisticsParameterData.do` 를 써야 한다. `statisticsData.do` 는 항상 err 20.",
            "🔴 인증키를 디코드하지 말 것 — base64 처럼 보여도 발급된 값 그대로 쓴다.",
            "🔴 모든 실패가 HTTP 200 이다. 성공은 배열, 실패는 {err, errMsg} 객체다.",
            "🔴 분류축이 여럿인 표는 objL 개수를 맞춰야 한다 — 모자라면 err 20 '(objL)', "
            "넘치면 err 21. kosis_data 가 자동으로 맞추지만, 축을 좁히려면 obj_l2~obj_l8 을 쓴다.",
            "⚠️ err 30(결과 없음)은 오류가 아니다 — 0건과 실패를 구분해서 보고한다.",
            "⚠️ 없는 메타 `type` 은 err 21 이 아니라 err 30 을 준다 — 오타와 0건이 구분되지 않는다.",
        ],
    }


@mcp.tool(annotations=_READ)
@_safe
def kosis_search(query: str, max_records: int = 30) -> dict:
    """통계표를 이름·내용으로 찾는다(KOSIS 통합검색).

    Args:
        query: 검색어(예: '사교육비', '출산율').
        max_records: 돌려줄 최대 건수.
    """
    if not get_api_key():
        return _NO_KEY
    tables, meta = KosisClient().search(query, max_records=max_records)
    return {
        "tables": [{**t.to_row(), "scoring_text": t.scoring_text()} for t in tables],
        "meta": meta,
        "다음_단계": "표를 골랐으면 kosis_meta 로 항목·분류·주기를 확인하고 "
                     "kosis_data 로 수치를 받으세요.",
    }


@mcp.tool(annotations=_READ)
@_safe
def kosis_list(vw_cd: str = "MT_ZTITLE", parent_id: str = "") -> dict:
    """통계목록 트리를 한 단계 훑는다(주제별·기관별 등).

    Args:
        vw_cd: 서비스뷰 코드. `kosis_guide` 의 `서비스뷰` 참조.
        parent_id: 상위 목록 ID. **비우면 최상위**가 온다(개발가이드는 필수라 하지만
            실제로는 생략 가능 — 실측).
    """
    if not get_api_key():
        return _NO_KEY
    tables, meta = KosisClient().list_tree(vw_cd, parent_id or None)
    return {
        "items": [{"list_id": t.raw.get("LIST_ID", ""), "name": t.tbl_nm,
                   "tbl_id": t.tbl_id, "org_id": t.org_id,
                   "is_folder": not t.tbl_id} for t in tables],
        "meta": meta,
        "안내": "is_folder 가 true 면 그 `list_id` 를 parent_id 로 넣어 한 단계 더 들어갑니다.",
    }


@mcp.tool(annotations=_READ)
@_safe
def kosis_meta(org_id: str, tbl_id: str, kind: str = "TBL") -> dict:
    """통계표의 메타자료 — 항목(ITM)·수록기간(PRD)·출처(SOURCE)·주석(CMMT) 등.

    ⚠️ `kosis_data` 를 부르기 전에 **ITM 으로 항목 ID 를, PRD 로 수록주기를** 확인하면
       err 20/21 을 피할 수 있다.
    🔴 **분류축을 알려 주는 종류는 없다.** `NCD` 는 분류가 아니라 신규수록 시점이고
       `OBJ`·`CLS` 는 err 30 이다(실측) — 축은 `kosis_data` 가 알아서 맞춘다.
    ⚠️ 없는 `kind` 는 err 21 이 아니라 err 30(0건)으로 오므로 오타가 '자료 없음'처럼
       보인다. 그래서 아는 종류만 받는다 — 가능한 값은 `kosis_guide` 의 `메타_종류`.
    """
    if not get_api_key():
        return _NO_KEY
    rows, meta = KosisClient().meta(org_id, tbl_id, kind)
    return {"rows": rows, "meta": meta}


@mcp.tool(annotations=_READ)
@_safe
def kosis_explain(org_id: str, tbl_id: str) -> dict:
    """통계설명(조사개요) — 목적·근거·주기·범위 등.

    통계표에는 초록이 없으므로, 관련도 채점이나 요약이 필요할 때 이 설명이 재료다.
    """
    if not get_api_key():
        return _NO_KEY
    rows, meta = KosisClient().explain(org_id, tbl_id)
    return {"rows": rows, "meta": meta}


@mcp.tool(annotations=_READ)
@_safe
def kosis_data(org_id: str, tbl_id: str, prd_se: str,
               start: str = "", end: str = "", recent: int = 0,
               obj_l1: str = "ALL", obj_l2: str = "", obj_l3: str = "",
               obj_l4: str = "", obj_l5: str = "", obj_l6: str = "",
               obj_l7: str = "", obj_l8: str = "",
               items: str = "ALL", max_rows: int = 20_000) -> dict:
    """통계표의 수치를 받는다.

    🔴 **분류축이 여럿인 표(예: 산업 × 규모)도 그냥 부르면 된다.** KOSIS 는 요청의
       분류축 개수가 표의 축 개수와 정확히 맞기를 요구하는데(모자라면 err 20 `(objL)`,
       넘치면 err 21) 축 개수를 알려 주는 메타가 없다. 그래서 이 도구가 **축을 하나씩
       늘려 가며 맞춘다** — 결과의 `meta.obj_levels` 에 확정된 축이 실린다.

    Args:
        org_id: 기관 ID(예: '101').
        tbl_id: 통계표 ID(예: 'DT_1B040A3').
        prd_se: 수록주기 — Y(년)·H(반기)·Q(분기)·M(월)·D(일). `kosis_meta(kind='PRD')` 로 확인.
        start, end: 시점 범위(예: '202101'~'202512'). 🔴 이 방식이면 4만 셀을 넘어도
            **자동으로 기간을 쪼개** 전수를 받는다.
        recent: 최근 N개 시점. start/end 대신 쓴다(이 방식은 자동 분할이 안 된다).
        obj_l1: 분류1 — 'ALL' 전체, '11' 특정, '11*' 하위 전체, '11+21' 여럿.
        obj_l2 ~ obj_l8: 분류2~8. **비워 두면 필요한 만큼 'ALL' 로 자동으로 채운다.**
            4만 셀(err 31)에 걸릴 때 특정 코드로 좁히는 자리이기도 하다.
        items: 항목 — 'ALL' 또는 항목 ID(`kosis_meta(kind='ITM')`).
        max_rows: 돌려줄 최대 행 수(파일로 받으려면 kosis_collect).
    """
    if not get_api_key():
        return _NO_KEY
    obs, meta = KosisClient().data(
        org_id, tbl_id, prd_se=prd_se,
        start=start or None, end=end or None,
        recent=recent or None, obj_l1=obj_l1,
        obj_levels=_obj_levels(obj_l2, obj_l3, obj_l4, obj_l5,
                               obj_l6, obj_l7, obj_l8),
        items=items, max_rows=max_rows)
    return {"observations": [o.to_row() for o in obs], "meta": meta}


@mcp.tool(annotations=_READ)
@_safe
def kosis_citation(org_id: str, tbl_id: str, accessed: str = "") -> dict:
    """통계표 하나를 **서지(인용) 칸으로 투영**한다 — 선택 기능.

    ⚠️ 이 도구는 부가 기능이다. 통계를 쓰는 것이 목적이면 `kosis_search`(표 메타)와
       `kosis_data`(수치)가 본령이고, 이 도구는 서지관리 도구로 넘길 때만 쓴다.

    KOSIS 는 조사(STAT_NM)와 표(TBL_NM) 두 층을 다 주므로 어느 층으로 인용할지는
    부르는 쪽이 고른다 — `container_title` 이 조사층, `title` 이 표층이다.

    Args:
        accessed: 조회일자(YYYY-MM-DD). 데이터셋 인용에는 조회일자가 필요하다.
    """
    if not get_api_key():
        return _NO_KEY
    client = KosisClient()
    tables, meta = client.search(tbl_id, max_records=50)
    hit = next((t for t in tables if t.tbl_id == tbl_id and t.org_id == org_id), None)
    if hit is None:
        rows, _ = client.meta(org_id, tbl_id, "TBL")
        if not rows:
            return {"found": False,
                    "reason": f"통계표 {org_id}/{tbl_id} 를 찾지 못했습니다."}
        from .models import Table
        hit = Table(tbl_id=tbl_id, org_id=org_id,
                    tbl_nm=str(rows[0].get("TBL_NM", "")),
                    url=f"https://kosis.kr/statHtml/statHtml.do?orgId={org_id}&tblId={tbl_id}")
    c = hit.citation_fields()
    c["accessed"] = accessed
    return {"found": True, "citation": c, "table": hit.to_row(),
            "note": "부가 기능입니다 — 통계 자체는 kosis_search·kosis_data 를 쓰세요."}


@mcp.tool(annotations=_WRITE)
@_safe
def kosis_collect(org_id: str, tbl_id: str, prd_se: str, out_dir: str,
                  start: str = "", end: str = "", recent: int = 0,
                  name: str = "kosis", formats: list[str] | None = None,
                  obj_l1: str = "ALL", obj_l2: str = "", obj_l3: str = "",
                  obj_l4: str = "", obj_l5: str = "", obj_l6: str = "",
                  obj_l7: str = "", obj_l8: str = "",
                  items: str = "ALL") -> dict:
    """통계표 수치를 받아 xlsx/csv/json/sqlite 로 저장한다.

    ⚠️ 분류 축이 표마다 다르므로 **열 구성이 가변**이다 — 분류 이름이 그대로 열이 된다.
       축 개수도 표마다 다르지만 `obj_l2`~`obj_l8` 을 비워 두면 자동으로 맞춘다.
    """
    if not get_api_key():
        return _NO_KEY
    client = KosisClient()
    obs, meta = client.data(org_id, tbl_id, prd_se=prd_se,
                            start=start or None, end=end or None,
                            recent=recent or None, obj_l1=obj_l1,
                            obj_levels=_obj_levels(obj_l2, obj_l3, obj_l4, obj_l5,
                                                   obj_l6, obj_l7, obj_l8),
                            items=items, max_rows=10 ** 9)
    # kind 를 준다 — 0건이어도 관측치 열 머리로 나가야 한다(빈 파일도 계약이다).
    paths = export(obs, formats or ["xlsx", "json"], out_dir, name,
                   kind="observation")
    return {"saved": paths, "count": len(obs), "meta": meta}


def _env_port(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw.isdigit() else None


def main(argv: list[str] | None = None) -> None:
    """⚠️ 기본 전송은 **stdio 로 못박는다** — 바뀌면 기존 MCP 등록이 전부 죽는다."""
    parser = argparse.ArgumentParser(prog="kosis-mcp", add_help=True)
    parser.add_argument("--transport", default=os.environ.get("KOSIS_MCP_TRANSPORT", "stdio"),
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--host", default=os.environ.get("KOSIS_MCP_HOST"))
    parser.add_argument("--port", type=int, default=_env_port("KOSIS_MCP_PORT"))
    args, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    if args.host:
        mcp.settings.host = args.host
    if args.port:
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
