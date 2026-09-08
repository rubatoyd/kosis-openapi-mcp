"""KOSIS 공유서비스 HTTP 클라이언트 — 호출·재시도·자동 분할.

이 파일이 감당하는 세 가지(전부 실측에서 나왔다):

1. **`jsonVD=Y` 를 항상 붙인다.** 빠뜨리면 JSON 이 아니라 자바스크립트 객체 리터럴이
   온다. 개발가이드의 입력 변수 표에 없는 파라미터라 놓치기 쉽다.
2. **`err 31`(4만 셀 초과)은 실패가 아니라 분할 신호다.** 시점을 반씩 쪼개 다시 부른다.
3. **분당 200건 제한**(err 40) — throttle 로 아예 닿지 않게 하고, 그래도 나면 기다린다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Sequence

import requests

from . import __version__
from .config import (
    DEFAULT_THROTTLE,
    ENDPOINTS,
    ERROR_CODES,
    MAX_CALLS_PER_TOOL_CALL,
    MAX_OBJ_LEVELS,
    META_TYPES,
    VIEW_CODES,
    get_api_key,
    install_log_scrubber,
    scrub,
    use_os_trust,
)
from .models import Observation, Table, observation_from_row, table_from_row
from .parser import (
    ApiError, MissingObjLevel, NoData, ParseError, RateLimited, TooManyCells,
    parse_rows)

log = logging.getLogger("kosis_mcp")


class KosisError(RuntimeError):
    """네트워크·HTTP·파싱을 아우르는 클라이언트 오류 (인증키는 절대 싣지 않는다)."""


class KosisClient:
    def __init__(self, *, api_key: str | None = None,
                 throttle: float = DEFAULT_THROTTLE, timeout: int = 40,
                 max_retries: int = 3,
                 max_calls: int = MAX_CALLS_PER_TOOL_CALL) -> None:
        use_os_trust()
        install_log_scrubber()
        self.api_key = (api_key or get_api_key() or "").strip()
        self.throttle = max(0.0, throttle)
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_calls = max(1, max_calls)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            f"kosis-openapi-mcp/{__version__} "
            f"(+https://github.com/rubatoyd/kosis-openapi-mcp)")
        self._last = 0.0
        self.calls = 0

    # ── 저수준 ───────────────────────────────────────────────────────────────
    def _require_key(self) -> None:
        """🔴 인증키 누락은 **영구 조건**이다 — 재시도 루프에 들여보내지 않는다."""
        if not self.api_key:
            raise KosisError(
                "KOSIS_API_KEY 가 설정되지 않았습니다. kosis.kr 회원가입 후 "
                "공유서비스 활용신청(자동 승인)에서 받은 인증키를 환경변수로 주세요. "
                "🔴 발급된 값을 **그대로** 쓰세요 — base64 처럼 보여도 디코드하면 안 됩니다.")

    def _sleep(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.throttle:
            time.sleep(self.throttle - gap)
        self._last = time.monotonic()

    def _check_budget(self) -> None:
        """🔴 한 번의 도구 호출이 낼 수 있는 요청 수를 **실제로** 막는다.

        ⚠️ `kosis_guide` 가 이 상한을 알리고 있었지만 코드 어디서도 검사하지 않았다.
           없는 안전장치를 있다고 알리는 것이 더 나쁘다. 자동 분할(err 31)은 기간을
           반씩 쪼개므로 넓은 구간 × 촘촘한 주기에서 요청이 기하급수로 늘 수 있다.
        """
        if self.calls >= self.max_calls:
            raise KosisError(
                f"이 도구 호출에서 API 요청이 상한({self.max_calls}회)에 닿았습니다. "
                f"기간을 좁히거나(start/end), 분류·항목을 지정해 한 번에 받는 양을 "
                f"줄이세요. 상한은 KOSIS_MAX_CALLS_PER_TOOL_CALL 로 조정합니다.")

    def _get(self, url: str, params: dict, *, what: str) -> list[dict]:
        """1회 호출 + 재시도 → 레코드 목록.

        ⚠️ `raise_for_status()` 를 쓰지 않는다 — requests 가 인증키가 든 **전체 URL** 을
           예외 메시지에 박는다. 상태코드만 본다. requests 예외도 **타입만** 남긴다.
        """
        self._require_key()
        self._check_budget()
        q = {"apiKey": self.api_key, "format": "json", "jsonVD": "Y", **params}
        last: Exception | None = None
        for attempt in range(self.max_retries):
            self._sleep()
            self.calls += 1
            try:
                resp = self._session.get(url, params=q, timeout=self.timeout)
            except requests.RequestException as e:
                last = KosisError(f"요청 실패: {type(e).__name__}")
                log.warning("네트워크 오류(%s) — 재시도 %d/%d",
                            type(e).__name__, attempt + 1, self.max_retries)
            else:
                if resp.status_code != 200:
                    last = KosisError(f"HTTP {resp.status_code}")
                else:
                    try:
                        return parse_rows(resp.content, what=what)
                    except (NoData, TooManyCells, MissingObjLevel):
                        raise                      # 호출자가 뜻있게 다룬다
                    except RateLimited as e:
                        last = e
                        wait = 5 * (attempt + 1)
                        log.warning("분당 한도 초과 — %d초 대기 후 재시도 %d/%d",
                                    wait, attempt + 1, self.max_retries)
                        time.sleep(wait)
                        continue
                    except ApiError as e:
                        if not e.retryable:
                            raise KosisError(str(e)) from None
                        last = e
                    except ParseError as e:
                        raise KosisError(str(e)) from None
            if attempt < self.max_retries - 1:
                time.sleep(self.throttle * (2 ** attempt) + 0.3)
        raise KosisError(scrub(str(last) if last else "알 수 없는 오류"))

    # ── 통계표 찾기 ──────────────────────────────────────────────────────────
    def search(self, query: str, *, max_records: int = 50) -> tuple[list[Table], dict]:
        """KOSIS 통합검색으로 **통계표**를 찾는다.

        ⚠️ 이 서비스에는 페이징 파라미터가 문서에 없다. 한 번에 오는 만큼이 전부이고,
           더 필요하면 검색어를 좁혀야 한다 — 그 사실을 meta 로 알린다.
        """
        q = (query or "").strip()
        if not q:
            raise KosisError("검색어가 비었습니다.")
        meta: dict[str, Any] = {"query": q, "service": "통합검색"}
        try:
            rows = self._get(ENDPOINTS["search"],
                             {"method": "getList", "searchNm": q},
                             what=f"통합검색('{q}')")
        except NoData:
            # 🔴 0건은 오류가 아니다. 다만 '0건'과 '미상'을 **구분해서** 돌려준다.
            meta.update(total=0, fetched=0, no_data=True,
                        note="조회 결과가 없습니다(err 30) — 오류가 아닙니다.")
            return [], meta
        tables = [table_from_row(r) for r in rows]
        meta["total"] = len(tables)
        if len(tables) > max_records:
            meta["truncated_note"] = (
                f"{len(tables):,}건 중 {max_records:,}건만 돌려줍니다 "
                f"(이 서비스에는 페이징이 없어 서버가 준 만큼이 전부입니다).")
            tables = tables[:max_records]
        meta["fetched"] = len(tables)
        return tables, meta

    def list_tree(self, vw_cd: str = "MT_ZTITLE", parent_id: str | None = None,
                  ) -> tuple[list[Table], dict]:
        """통계목록 트리를 한 단계 훑는다.

        ⚠️ 개발가이드는 `parentListId` 를 **필수**라 적지만 실제로는 생략할 수 있고,
           생략하면 **최상위 목록**이 온다(실측). 뿌리부터 훑을 때 이것이 입구다.
        """
        if vw_cd not in VIEW_CODES:
            raise KosisError(
                f"알 수 없는 서비스뷰: {vw_cd!r}. 가능한 값: "
                + ", ".join(f"{k}({v})" for k, v in VIEW_CODES.items()))
        params = {"method": "getList", "vwCd": vw_cd}
        if parent_id:
            params["parentListId"] = parent_id
        meta = {"vw_cd": vw_cd, "vw_nm": VIEW_CODES[vw_cd], "parent_id": parent_id or "(최상위)"}
        try:
            rows = self._get(ENDPOINTS["list"], params,
                             what=f"통계목록({vw_cd}/{parent_id or '최상위'})")
        except NoData:
            meta.update(total=0, no_data=True,
                        note="해당 목록에 자료가 없습니다(err 30) — 오류가 아닙니다.")
            return [], meta
        # 목록 응답은 하위 '목록'과 '통계표'가 섞여 온다 — TBL_ID 유무로 가른다.
        tables = [table_from_row(r, vw_cd=vw_cd) for r in rows]
        meta["total"] = len(tables)
        meta["folders"] = sum(1 for t in tables if not t.tbl_id)
        meta["tables"] = sum(1 for t in tables if t.tbl_id)
        return tables, meta

    # ── 메타 ─────────────────────────────────────────────────────────────────
    def meta(self, org_id: str, tbl_id: str, kind: str = "TBL") -> tuple[list[dict], dict]:
        """통계표 메타자료. `kind` 는 TBL·ITM·PRD·NCD·ORG·UNIT·SOURCE·CMMT·WGT."""
        k = (kind or "TBL").upper()
        if k not in META_TYPES:
            raise KosisError(
                f"알 수 없는 메타 종류: {kind!r}. 가능한 값: "
                + ", ".join(f"{a}({b})" for a, b in META_TYPES.items()))
        try:
            rows = self._get(ENDPOINTS["meta"],
                             {"method": "getMeta", "type": k,
                              "orgId": org_id, "tblId": tbl_id},
                             what=f"메타({k}) {org_id}/{tbl_id}")
        except NoData:
            return [], {"kind": k, "label": META_TYPES[k], "total": 0, "no_data": True}
        return rows, {"kind": k, "label": META_TYPES[k], "total": len(rows)}

    def explain(self, org_id: str, tbl_id: str) -> tuple[list[dict], dict]:
        """통계설명(조사개요) — 관련도 채점의 재료가 된다(통계표에는 초록이 없다)."""
        try:
            rows = self._get(ENDPOINTS["expl"],
                             {"method": "getList", "orgId": org_id, "tblId": tbl_id},
                             what=f"통계설명 {org_id}/{tbl_id}")
        except NoData:
            return [], {"total": 0, "no_data": True,
                        "note": "이 통계표에는 설명자료가 없습니다(err 30)."}
        return rows, {"total": len(rows)}

    # ── 관측치 (자동 분할) ───────────────────────────────────────────────────
    def data(self, org_id: str, tbl_id: str, *, prd_se: str,
             obj_l1: str = "ALL", items: str = "ALL",
             start: str | None = None, end: str | None = None,
             recent: int | None = None,
             obj_levels: dict[str, str] | None = None,
             max_rows: int = 100_000) -> tuple[list[Observation], dict]:
        """통계표의 수치를 받는다 — **`/openapi/Param/` 경로**를 쓴다.

        🔴 `statisticsData.do` 로 orgId/tblId 를 보내면 **항상 err 20** 이다(실측).
           개발가이드가 입력 변수 표를 그 URL 아래 싣고 있어 반나절을 버리기 쉽다.

        🔴 한 요청은 **4만 셀 이하**여야 한다(err 31). 기간을 준 경우에는 초과 시
           **자동으로 절반씩 쪼개** 다시 부른다 — 호출자가 신경 쓰지 않아도 된다.

        🔴 **분류축 개수는 표마다 다르고 정확히 맞아야 한다.** 모자라면 err 20 `(objL)`,
           넘치면 err 21 이다(실측). 축 개수를 알려 주는 메타 서비스가 없으므로
           err 20 `(objL)` 을 만나면 **축을 하나씩 늘려 가며** 다시 부른다 — 이것도
           호출자가 신경 쓸 일이 아니다. 실측: 임금 표(산업분류 × 규모별)는 축 2개에서
           34,496행을 준다. `obj_levels` 로 직접 지정하면 그 지점에서 출발한다.
        """
        self._require_key()
        if not (start and end) and not recent:
            raise KosisError(
                "조회 기간이 필요합니다 — `start`/`end`(시점 범위) 또는 "
                "`recent`(최근 N개 시점) 중 하나를 주세요.")

        base = {"method": "getList", "orgId": org_id, "tblId": tbl_id,
                "objL1": obj_l1, "itmId": items, "prdSe": prd_se}
        # 🔴 objL2~objL8 은 base 에 굽지 않는다 — 자동 승급이 요청마다 얹어야 하고,
        #    한 번 확정되면 뒤따르는 분할 요청들이 그것을 그대로 물려받아야 한다.
        state: dict[str, Any] = {
            "levels": {k: v for k, v in (obj_levels or {}).items() if v},
            "escalated": False,
        }

        meta: dict[str, Any] = {"org_id": org_id, "tbl_id": tbl_id, "prd_se": prd_se,
                                "requests": 0, "chunks": [], "split_note": None,
                                "obj_levels": None, "obj_note": None}
        rows: list[dict] = []

        if recent and not (start and end):
            # 최근 N개 시점 — 쪼갤 축이 없으므로 초과하면 N 을 줄이라고 알린다.
            try:
                rows = self._fetch(base | {"newEstPrdCnt": str(int(recent))}, meta, state)
            except TooManyCells:
                raise KosisError(
                    f"요청이 4만 셀 제한을 넘습니다(err 31). `recent={recent}` 를 줄이거나, "
                    f"`start`/`end` 로 기간을 주면 이 도구가 자동으로 쪼갭니다.") from None
        else:
            rows = self._fetch_range(base, str(start), str(end), meta, state)

        meta["obj_levels"] = {"objL1": obj_l1, **state["levels"]}
        if state["escalated"]:
            meta["obj_note"] = (
                f"이 표는 분류축이 {len(state['levels']) + 1}개입니다 — "
                f"err 20(objL)을 보고 자동으로 {', '.join(state['levels'])} 를 "
                f"'ALL' 로 채워 받았습니다. 축을 좁히려면 obj_l2~obj_l8 에 코드를 주세요.")

        obs = [observation_from_row(r) for r in rows]
        meta["total"] = len(obs)
        if len(obs) > max_rows:
            meta["truncated_note"] = (
                f"{len(obs):,}행 중 {max_rows:,}행만 돌려줍니다(max_rows).")
            obs = obs[:max_rows]
        meta["fetched"] = len(obs)
        return obs, meta

    def _fetch(self, params: dict, meta: dict, state: dict) -> list[dict]:
        """1회 요청 — err 20 `(objL)` 이면 **분류축을 하나 늘려** 다시 부른다.

        🔴 표의 분류축 개수를 알려 주는 메타 서비스가 없어서(‘NCD’ 는 분류가 아니라
           신규수록 시점이고 ‘OBJ’·‘CLS’ 는 err 30) 맞혀 보는 수밖에 없다. 모자라면
           err 20, 넘치면 err 21 이므로 **아래에서 위로** 올라가는 방향만 안전하다.

        확정된 축은 `state` 에 남아 뒤따르는 분할 요청이 그대로 물려받는다 —
        60개월 구간이 3번 쪼개져도 축 탐색은 처음 한 번뿐이다.
        """
        what = f"통계자료 {params.get('orgId')}/{params.get('tblId')}"
        while True:
            meta["requests"] += 1
            try:
                return self._get(ENDPOINTS["param"], params | state["levels"], what=what)
            except MissingObjLevel:
                used = {int(k[4:]) for k in state["levels"]
                        if k.startswith("objL") and k[4:].isdigit()}
                nxt = max(used | {1}) + 1
                if nxt > MAX_OBJ_LEVELS:
                    raise KosisError(
                        f"분류축을 objL{MAX_OBJ_LEVELS} 까지 늘려도 KOSIS 가 "
                        f"'필수요청변수 누락(objL)'을 돌려줍니다 — 이 표의 축 구성을 "
                        f"kosis.kr 통계표 화면에서 확인해 obj_l2~obj_l8 로 직접 주세요."
                    ) from None
                state["levels"][f"objL{nxt}"] = "ALL"
                state["escalated"] = True
                log.info("err 20(objL) — 분류축을 objL%d 까지 늘려 재시도", nxt)

    def _fetch_range(self, base: dict, start: str, end: str, meta: dict,
                     state: dict, depth: int = 0) -> list[dict]:
        """기간을 주고 받아오되, err 31 이면 **절반으로 쪼개** 재귀한다."""
        try:
            out = self._fetch(base | {"startPrdDe": start, "endPrdDe": end}, meta, state)
            meta["chunks"].append({"start": start, "end": end, "rows": len(out)})
            return out
        except NoData:
            meta["chunks"].append({"start": start, "end": end, "rows": 0, "no_data": True})
            return []
        except TooManyCells:
            if start == end or depth > 12:
                raise KosisError(
                    f"단일 시점({start})만으로도 4만 셀 제한을 넘습니다 — "
                    f"분류(obj_l1~obj_l8)나 항목(items)을 좁혀 주세요. "
                    f"'ALL' 대신 특정 코드를 쓰면 줄어듭니다.") from None
            mid = _midpoint(start, end)
            if mid is None or mid == end:
                raise KosisError(
                    f"기간 {start}~{end} 가 4만 셀 제한을 넘는데 더 쪼갤 수 없습니다 — "
                    f"분류나 항목을 좁혀 주세요.") from None
            meta["split_note"] = (
                "4만 셀 제한(err 31)에 걸려 기간을 자동으로 쪼개 받았습니다 — "
                "결과는 합쳐진 전수입니다.")
            log.info("err 31 — 기간 분할 %s~%s → %s | %s~%s", start, end, mid, mid, end)
            left = self._fetch_range(base, start, mid, meta, state, depth + 1)
            right = self._fetch_range(base, _next_period(mid, base.get("prdSe", "")),
                                      end, meta, state, depth + 1)
            return left + right

    # ── 상태 ─────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        info: dict[str, Any] = {
            "has_api_key": bool(self.api_key),
            "api": "KOSIS 공유서비스 (kosis.kr/openapi)",
            "rate_limit": "분당 200건",
            "cell_limit": "요청당 4만 셀",
        }
        if not self.api_key:
            info["ok"] = False
            info["note"] = "KOSIS_API_KEY 미설정."
            return info
        try:
            rows = self._get(ENDPOINTS["list"],
                             {"method": "getList", "vwCd": "MT_ZTITLE"},
                             what="상태 점검")
            info["ok"] = True
            info["probe"] = {"service": "통계목록 최상위", "rows": len(rows)}
            info["note"] = "인증키 유효 — 정상 응답."
        except Exception as e:  # noqa: BLE001
            info["ok"] = False
            info["note"] = scrub(f"{type(e).__name__}: {e}")
        return info


# ── 기간 계산 ───────────────────────────────────────────────────────────────
def _midpoint(start: str, end: str) -> str | None:
    """두 수록시점의 중간. 자릿수가 같아야 하고, 숫자로만 이루어져야 한다."""
    if not (start.isdigit() and end.isdigit() and len(start) == len(end)):
        return None
    a, b = int(start), int(end)
    if a >= b:
        return None
    if len(start) == 4:                    # 연
        return str((a + b) // 2)
    if len(start) == 6:                    # 연월/연분기 — 월 수로 환산해 나눈다
        ya, ma = a // 100, a % 100
        yb, mb = b // 100, b % 100
        ta, tb = ya * 12 + ma, yb * 12 + mb
        tm = (ta + tb) // 2
        y, m = divmod(tm - 1, 12)
        return f"{y:04d}{m + 1:02d}"
    if len(start) == 8:                    # 연월일 — 대략 중간(정확할 필요 없다)
        return str((a + b) // 2)
    return None


def _next_period(period: str, prd_se: str) -> str:
    """다음 수록시점 — 분할한 왼쪽 구간과 겹치지 않게."""
    s = str(period)
    if not s.isdigit():
        return s
    if len(s) == 4:
        return str(int(s) + 1)
    if len(s) == 6:
        y, m = int(s[:4]), int(s[4:])
        step = {"Q": 1, "H": 1}.get((prd_se or "").upper(), 1)
        limit = {"Q": 4, "H": 2}.get((prd_se or "").upper(), 12)
        m += step
        if m > limit:
            y, m = y + 1, 1
        return f"{y:04d}{m:02d}"
    return str(int(s) + 1)
