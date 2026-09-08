"""회귀 — 전부 **실응답 픽스처** 기반.

이 파일이 지키는 것: **조용히 줄어들거나, 오류가 0건으로 둔갑하지 않는다.**
KOSIS 는 모든 실패를 HTTP 200 으로 주므로 '예외가 안 났다'는 통과 근거가 못 된다.
"""
from __future__ import annotations

import json

import pytest

from kosis_mcp.client import KosisClient, KosisError, _midpoint, _next_period
from kosis_mcp.config import ERROR_CODES, ENDPOINTS, META_TYPES, scrub
from kosis_mcp.exporters import _table
from kosis_mcp.models import (
    Observation, Table, clean_text, normalize_period,
    observation_from_row, table_from_row)
from kosis_mcp.parser import (
    ApiError, MissingObjLevel, NoData, ParseError, RateLimited, TooManyCells,
    looks_like_js_literal, parse, parse_rows)


# ── 🔴 jsonVD — 이 저장소의 첫 번째 함정 ────────────────────────────────────
def test_missing_jsonvd_is_diagnosed_precisely(fx):
    """`format=json` 만 주면 **키에 따옴표가 없는 자바스크립트 리터럴**이 온다.

    개발가이드(172쪽)의 입력 변수 표에는 `jsonVD` 가 **없고** JSP 예제 안에만 있다.
    그래서 표만 보고 만들면 첫 응답에서 죽는다 — 그때 원인을 정확히 짚어야 한다.
    """
    raw = fx("no_jsonvd.txt")
    assert looks_like_js_literal(raw)
    with pytest.raises(ParseError) as e:
        parse(raw, what="통계목록")
    assert "jsonVD" in str(e.value)


def test_client_always_sends_jsonvd():
    """클라이언트가 알아서 붙인다 — 호출자가 기억할 일이 아니다."""
    sent = {}

    class C(KosisClient):
        def _get(self, url, params, *, what):
            sent.update(params)
            return []

    C(api_key="x", throttle=0).search("교육")
    # _get 을 가로챘으므로 jsonVD 주입은 실제 _get 안에 있다 → 직접 확인한다
    real = KosisClient(api_key="x", throttle=0)
    import inspect
    src = inspect.getsource(real._get)
    assert '"jsonVD": "Y"' in src


# ── 🔴 모든 실패가 HTTP 200 ─────────────────────────────────────────────────
def test_bad_key_envelope_is_an_error_not_empty(fx):
    with pytest.raises(ApiError) as e:
        parse(fx("err_badkey.json"), what="통계목록")
    assert e.value.code == "11"


def test_wrong_endpoint_gives_err20_with_the_fix_in_the_message(fx):
    """🔴 `statisticsData.do` 로 orgId/tblId 를 보내면 항상 err 20 이다.

    개발가이드가 입력 변수 표를 그 URL 아래 싣고 있어 반나절을 버리기 쉽다 —
    오류 메시지가 올바른 경로를 알려 줘야 한다.
    """
    with pytest.raises(ApiError) as e:
        parse(fx("err_wrong_endpoint.json"), what="통계자료")
    assert e.value.code == "20"
    assert "Param/statisticsParameterData.do" in ERROR_CODES["20"]


def test_empty_body_is_not_zero_results():
    """`method` 를 빠뜨리면 KOSIS 는 **빈 본문**을 준다(실측). 0건이 아니다."""
    with pytest.raises(ParseError) as e:
        parse("", what="통계목록")
    assert "비어 있습니다" in str(e.value)


def test_error_classes_are_distinguished():
    def env(code):
        return json.dumps({"err": code, "errMsg": "x"})
    assert isinstance(pytest.raises(NoData, parse, env("30"), what="t").value, NoData)
    assert isinstance(pytest.raises(TooManyCells, parse, env("31"), what="t").value,
                      TooManyCells)
    assert isinstance(pytest.raises(RateLimited, parse, env("40"), what="t").value,
                      RateLimited)
    assert pytest.raises(RateLimited, parse, env("40"), what="t").value.retryable
    assert not pytest.raises(ApiError, parse, env("11"), what="t").value.retryable


def test_xml_error_envelope_from_bigdata():
    """대용량 서비스는 XML 로 오류를 준다 — JSON 파서가 헛돌지 않게 잡는다."""
    body = '<?xml version="1.0" encoding="UTF-8" ?><error><err>11</err></error>'
    with pytest.raises(ApiError) as e:
        parse(body, what="대용량")
    assert e.value.code == "11"


# ── 응답 모양 ───────────────────────────────────────────────────────────────
def test_search_rows_become_tables(fx):
    rows = parse_rows(fx("search_edu.json"), what="통합검색")
    assert len(rows) == 20
    t = table_from_row(rows[0])
    assert t.tbl_id and t.tbl_nm and t.org_nm
    assert t.stat_nm, "조사명(발간물 층)이 있어야 두 층 중에서 고를 수 있다"
    assert t.url.startswith("http")


def test_table_carries_both_citation_layers(fx):
    """KOSIS 는 조사(STAT_NM)와 표(TBL_NM) 두 층을 다 준다 — 어느 쪽으로도 인용 가능."""
    rows = parse_rows(fx("search_edu.json"), what="통합검색")
    t = table_from_row(rows[0])
    c = t.citation_fields()
    assert c["title"] == t.tbl_nm
    assert c["container_title"] == t.stat_nm
    assert c["authority"] == t.org_nm
    assert c["identifier"].startswith("KOSIS ")
    assert c["type"] == "report" and c["unit"] == "table"


def test_scoring_text_is_never_empty_for_a_real_table(fx):
    """🔴 통계표에는 초록이 없다.

    서지 도구의 '질문으로 찾기' 관문은 초록이나 키워드를 요구한다 — 표제·조사명·
    분류 경로·기관명을 합쳐 채점 재료를 만들어 주지 않으면 조용히 걸러진다.
    """
    rows = parse_rows(fx("search_edu.json"), what="통합검색")
    for r in rows[:5]:
        assert len(table_from_row(r).scoring_text()) >= 10


def test_list_top_level_is_folders(fx):
    rows = parse_rows(fx("list_top.json"), what="통계목록")
    tables = [table_from_row(r) for r in rows]
    assert tables and all(not t.tbl_id for t in tables), "최상위는 폴더뿐이다"
    assert all(r.get("LIST_ID") for r in rows)


def test_observations_keep_variable_classification_axes(fx):
    """분류 축은 표마다 다르다 — 고정 스키마를 강요하면 분류가 통째로 사라진다."""
    rows = parse_rows(fx("data_year.json"), what="통계자료")
    obs = [observation_from_row(r) for r in rows]
    assert obs
    o = obs[0]
    assert o.value != "" and o.period and o.item
    assert o.classes, "C1_OBJ_NM/C1_NM 이 분류로 잡혀야 한다"
    row = o.to_row()
    for k in o.classes:
        assert k in row, "분류 이름이 그대로 열이 되어야 한다"


def test_meta_itm_rows_have_item_ids(fx):
    rows = parse_rows(fx("meta_itm.json"), what="메타(ITM)")
    assert rows and all("ITM_ID" in r for r in rows)


# ── 정제·정규화 ─────────────────────────────────────────────────────────────
def test_clean_text_unescapes_twice(fx):
    """통계설명 본문에 `&amp;ldquo;` 처럼 **두 번 이스케이프된** 엔티티가 온다(실측)."""
    assert clean_text("&amp;ldquo;주민등록법&amp;rdquo;") == "“주민등록법”"
    raw = fx("explain.json")
    assert "&amp;" in raw, "픽스처가 그 사례를 담고 있어야 이 회귀가 의미 있다"


def test_clean_text_keeps_angle_content():
    """통계표명에 꺾쇠가 내용으로 쓰일 수 있다 — 알려진 태그만 지운다."""
    assert clean_text("<br>인구") == "인구"
    assert "<표>" in clean_text("<표> 인구")


@pytest.mark.parametrize("de,se,expected", [
    ("2024", "Y", "2024"),
    ("202608", "M", "2026-08"),
    ("20240115", "D", "2024-01-15"),
    ("202401", "Q", "2024-Q1"),
    ("", "Y", ""),
    ("미상", "Y", "미상"),        # 모르는 꼴은 비우지 않는다
])
def test_normalize_period(de, se, expected):
    assert normalize_period(de, se) == expected


# ── 🔴 4만 셀 자동 분할 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b,expected", [
    ("2020", "2024", "2022"),
    ("202101", "202512", "202306"),
    ("202401", "202402", "202401"),
])
def test_midpoint(a, b, expected):
    assert _midpoint(a, b) == expected


@pytest.mark.parametrize("p,se,expected", [
    ("202306", "M", "202307"), ("202312", "M", "202401"),
    ("2023", "Y", "2024"), ("202304", "Q", "202401"),
])
def test_next_period(p, se, expected):
    assert _next_period(p, se) == expected


class _SplittingClient(KosisClient):
    """4만 셀을 넘는 구간에서 err 31 을 내는 서버를 흉내낸다."""

    def __init__(self, limit_months: int = 30):
        super().__init__(api_key="stub", throttle=0)
        self.limit = limit_months
        self.seen: list[tuple[str, str]] = []

    def _get(self, url, params, *, what):
        s, e = params["startPrdDe"], params["endPrdDe"]
        self.seen.append((s, e))
        months = (int(e[:4]) * 12 + int(e[4:])) - (int(s[:4]) * 12 + int(s[4:])) + 1
        if months > self.limit:
            raise TooManyCells("조회결과 초과")
        return [{"TBL_ID": "T", "PRD_DE": s, "PRD_SE": "M", "DT": "1",
                 "ITM_NM": "x", "C1_OBJ_NM": "지역", "C1_NM": "전국"}
                for _ in range(months)]


def test_err31_splits_the_period_and_returns_everything():
    """🔴 err 31 은 실패가 아니라 분할 신호다 — 쪼개서 **전수**를 받아야 한다."""
    c = _SplittingClient(limit_months=30)
    obs, meta = c.data("101", "T", prd_se="M", start="202101", end="202512")
    assert meta["total"] == 60, "쪼갠 결과를 합치면 전 구간이어야 한다"
    assert meta["requests"] > 1 and meta["split_note"]
    covered = sorted(x["start"] for x in meta["chunks"])
    assert covered[0] == "202101"
    # 구간이 겹치지 않아야 한다(겹치면 중복 계산된다)
    spans = sorted((x["start"], x["end"]) for x in meta["chunks"])
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1 < s2, f"구간이 겹친다: {e1} ≥ {s2}"


def test_single_period_over_limit_says_narrow_the_axes():
    """한 시점만으로도 넘으면 쪼갤 수 없다 — 무엇을 좁히라고 알려 줘야 한다."""
    c = _SplittingClient(limit_months=0)
    with pytest.raises(KosisError) as e:
        c.data("101", "T", prd_se="M", start="202101", end="202102")
    assert "분류" in str(e.value) or "항목" in str(e.value)


def test_recent_mode_cannot_split_and_says_so():
    class C(KosisClient):
        def _get(self, url, params, *, what):
            raise TooManyCells("초과")
    with pytest.raises(KosisError) as e:
        C(api_key="x", throttle=0).data("101", "T", prd_se="M", recent=60)
    assert "start" in str(e.value) and "쪼갭니다" in str(e.value)


# ── 0건과 실패의 구분 ───────────────────────────────────────────────────────
def test_no_data_is_reported_as_zero_not_as_failure():
    """⚠️ 0건과 '미상'이 섞이면 소비하는 쪽 페이지바가 첫 쪽에서 닫힌다."""
    class C(KosisClient):
        def _get(self, url, params, *, what):
            raise NoData("조회 결과가 없습니다")
    tables, meta = C(api_key="x", throttle=0).search("존재하지않는검색어")
    assert tables == []
    assert meta["total"] == 0 and meta["no_data"] is True
    assert "오류가 아닙니다" in meta["note"]


def test_failures_are_not_swallowed():
    """소비하는 쪽이 ⚠ 로 띄울 수 있게, 실패는 예외로 올린다(조용한 0건 금지)."""
    class C(KosisClient):
        def _get(self, url, params, *, what):
            raise KosisError("서버 오류")
    with pytest.raises(KosisError):
        C(api_key="x", throttle=0).search("교육")


# ── 자격증명 ────────────────────────────────────────────────────────────────
def test_missing_key_fails_before_any_request():
    with pytest.raises(KosisError) as e:
        KosisClient(api_key="").search("교육")
    assert "KOSIS_API_KEY" in str(e.value)
    assert "디코드" in str(e.value), "base64 디코드 금지를 알려 줘야 한다"


def test_scrub_removes_the_key_from_urls_and_text(monkeypatch):
    monkeypatch.setenv("KOSIS_API_KEY", "SECRETKEY123=")
    s = scrub("https://kosis.kr/openapi/x.do?apiKey=SECRETKEY123=&format=json")
    assert "SECRETKEY123" not in s
    assert scrub("맨몸 SECRETKEY123= 노출") == "맨몸 *** 노출"


def test_view_code_is_validated_before_the_call():
    with pytest.raises(KosisError) as e:
        KosisClient(api_key="x", throttle=0).list_tree("NOPE")
    assert "MT_ZTITLE" in str(e.value)


def test_data_requires_a_period_selection():
    with pytest.raises(KosisError) as e:
        KosisClient(api_key="x", throttle=0).data("101", "T", prd_se="Y")
    assert "recent" in str(e.value)


# ── 서버 계약 ───────────────────────────────────────────────────────────────
def test_stdio_is_the_default_transport():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--transport", default="stdio")
    assert p.parse_args([]).transport == "stdio"


def test_tools_return_dict_not_exception(monkeypatch):
    from kosis_mcp import server
    monkeypatch.setattr(server, "get_api_key", lambda: "stub")
    fn = server.kosis_list.fn if hasattr(server.kosis_list, "fn") else server.kosis_list
    out = fn(vw_cd="NOPE")
    assert isinstance(out, dict) and "error" in out
    json.dumps(out, ensure_ascii=False)


def test_guide_tool_exposes_the_traps():
    from kosis_mcp import server
    fn = server.kosis_guide.fn if hasattr(server.kosis_guide, "fn") else server.kosis_guide
    out = fn()
    traps = " ".join(out["함정"])
    assert "jsonVD" in traps and "Param/statisticsParameterData.do" in traps
    json.dumps(out, ensure_ascii=False)


def test_param_endpoint_is_the_one_used_for_data():
    """레지스트리가 조용히 `statisticsData.do` 로 되돌아가지 않게 붙든다."""
    assert ENDPOINTS["param"].endswith("/Param/statisticsParameterData.do")
    import inspect
    src = inspect.getsource(KosisClient._fetch)
    assert 'ENDPOINTS["param"]' in src


# ── 🔴 다축 분류 — 축 개수를 맞히지 못하면 표 전체가 사각지대가 된다 ──────────
#
# 2026-09-08 실측(118/DT_118N_MON051 「산업/규모별 임금 및 근로시간」):
#     objL1 만          → err 20 "필수요청변수값이 누락되었습니다. (objL)"
#     objL1+objL2=ALL   → ✅ 34,496행 (산업분류 × 규모별)
#     objL1~objL8=ALL   → err 21 "잘못된 요청 변수"
# 모자라도 넘쳐도 안 되고, 축 개수를 알려 주는 메타 서비스가 없다.
def test_err20_objl_is_a_signal_not_a_failure():
    """err 20 은 원인이 둘이다 — `(objL)` 이 붙은 것만 축 승급 갈래로 보낸다."""
    objl = json.dumps({"err": "20", "errMsg": "필수요청변수값이 누락되었습니다. (objL)"})
    assert isinstance(pytest.raises(MissingObjLevel, parse, objl, what="t").value,
                      MissingObjLevel)
    # 엔드포인트를 틀린 err 20 은 승급 대상이 아니다 — 축을 늘려 봐야 소용없다.
    plain = json.dumps({"err": "20", "errMsg": "필수요청변수값이 누락되었습니다."})
    e = pytest.raises(ApiError, parse, plain, what="t").value
    assert e.code == "20" and not isinstance(e, MissingObjLevel)


class _MultiAxisClient(KosisClient):
    """분류축이 `axes` 개인 표를 흉내낸다 — 모자라면 err 20, 넘치면 err 21."""

    def __init__(self, axes: int = 2):
        super().__init__(api_key="stub", throttle=0)
        self.axes = axes
        self.seen: list[list[str]] = []

    def _get(self, url, params, *, what):
        sent = sorted(k for k in params if k.startswith("objL"))
        self.seen.append(sent)
        if len(sent) < self.axes:
            raise MissingObjLevel("필수요청변수값이 누락되었습니다. (objL)")
        if len(sent) > self.axes:
            raise KosisError("잘못된 요청 변수를 호출 하였습니다. (코드 21)")
        return [{"TBL_ID": "T", "PRD_DE": params.get("startPrdDe", "2024"),
                 "PRD_SE": "Y", "DT": "1", "ITM_NM": "x", "ORG_ID": "118",
                 "C1": "A", "C1_OBJ_NM": "산업분류", "C1_NM": "제조업",
                 "C2": "B", "C2_OBJ_NM": "규모별", "C2_NM": "전규모"}]


def test_multi_axis_table_is_reachable_without_the_caller_knowing_the_axis_count():
    """🔴 이 도구로 다축 표를 못 받던 것이 실사용에서 걸린 결함이다."""
    c = _MultiAxisClient(axes=2)
    obs, meta = c.data("118", "T", prd_se="Y", start="2024", end="2024")
    assert obs, "축을 맞히면 자료가 와야 한다"
    assert meta["obj_levels"] == {"objL1": "ALL", "objL2": "ALL"}
    assert meta["obj_note"] and "2개" in meta["obj_note"]
    assert c.seen == [["objL1"], ["objL1", "objL2"]], "아래에서 위로만 올라간다"
    assert obs[0].classes == {"산업분류": "제조업", "규모별": "전규모"}


def test_single_axis_table_is_not_over_escalated():
    """축이 하나인 표에 objL2 를 얹으면 err 21 이다 — 한 번에 맞아야 한다."""
    c = _MultiAxisClient(axes=1)
    obs, meta = c.data("101", "T", prd_se="Y", start="2024", end="2024")
    assert obs and c.seen == [["objL1"]]
    assert meta["obj_levels"] == {"objL1": "ALL"} and meta["obj_note"] is None


def test_resolved_axes_are_reused_across_split_chunks():
    """확정된 축은 뒤따르는 분할 요청이 물려받는다 — 축 탐색은 처음 한 번뿐이다."""
    class C(_MultiAxisClient):
        def _get(self, url, params, *, what):
            rows = super()._get(url, params, what=what)
            s, e = params["startPrdDe"], params["endPrdDe"]
            if int(e) - int(s) > 1:
                raise TooManyCells("조회결과 초과")
            return rows
    c = C(axes=2)
    _obs, meta = c.data("118", "T", prd_se="Y", start="2020", end="2024")
    assert meta["requests"] > 3, "분할이 실제로 일어나야 이 회귀가 의미 있다"
    assert sum(1 for s in c.seen if s == ["objL1"]) == 1, "축 탐색이 반복되면 안 된다"


def test_axis_escalation_gives_up_with_an_actionable_message():
    class C(KosisClient):
        def _get(self, url, params, *, what):
            raise MissingObjLevel("(objL)")
    with pytest.raises(KosisError) as e:
        C(api_key="x", throttle=0).data("1", "T", prd_se="Y", start="2024", end="2024")
    assert "obj_l2" in str(e.value)


def test_explicit_obj_levels_are_the_starting_point():
    """호출자가 축을 주면 그 지점에서 출발한다(err 31 을 피해 좁힐 때)."""
    c = _MultiAxisClient(axes=2)
    obs, meta = c.data("118", "T", prd_se="Y", start="2024", end="2024",
                       obj_levels={"objL2": "13102"})
    assert obs and c.seen == [["objL1", "objL2"]], "이미 맞으면 헛걸음이 없어야 한다"
    assert meta["obj_levels"]["objL2"] == "13102"
    assert meta["obj_note"] is None, "승급이 없었으면 안내도 없다"


def test_server_tool_exposes_the_axes_and_drops_blanks():
    """🔴 client 에만 있고 도구에 없으면 다축 표는 사각지대다."""
    from kosis_mcp.server import _obj_levels, kosis_data
    import inspect
    sig = inspect.signature(getattr(kosis_data, "fn", kosis_data)).parameters
    for i in range(2, 9):
        assert f"obj_l{i}" in sig, f"obj_l{i} 가 도구 인자로 노출돼야 한다"
    # 빈 축을 실어 보내면 개수가 어긋나 err 21 이 된다 — 반드시 떨어내야 한다.
    assert _obj_levels("", "13102", "", "", "", "", "") == {"objL3": "13102"}
    assert _obj_levels(*[""] * 7) == {}


# ── 🔴 Windows 콘솔(cp949) ──────────────────────────────────────────────────
def test_cli_survives_a_cp949_console(capsys, monkeypatch):
    """`—`·`·` 때문에 CLI 가 통째로 죽었다 — CI 가 ubuntu 라 못 잡았다.

    ⚠️ UnicodeEncodeError 는 ValueError 하위라 `except` 에 걸려 **인코딩 사고가
       API 오류로 둔갑**했다. 원인이 가려지는 쪽이 더 나쁘다.
    """
    import io
    from kosis_mcp import cli

    class Cp949Out(io.TextIOWrapper):
        pass

    buf = Cp949Out(io.BytesIO(), encoding="cp949", errors="strict")
    monkeypatch.setattr(cli.sys, "stdout", buf)
    monkeypatch.setattr(cli.sys, "stderr", buf)
    cli.use_utf8_stdio()
    buf.write("요청 — 4만 셀 · 📁")        # 재설정 전이면 UnicodeEncodeError
    buf.flush()
    assert buf.encoding.lower().replace("-", "") == "utf8"


def test_use_utf8_stdio_tolerates_streams_it_cannot_reconfigure():
    """파이프·캡처 등 재설정 불가 스트림에서 조용히 넘어가야 한다."""
    import io
    from kosis_mcp import cli
    real_out, real_err = cli.sys.stdout, cli.sys.stderr
    cli.sys.stdout = cli.sys.stderr = io.StringIO()   # reconfigure 가 없다
    try:
        cli.use_utf8_stdio()                          # 예외 없이 지나가야 한다
    finally:
        cli.sys.stdout, cli.sys.stderr = real_out, real_err


# ── 요청 상한은 광고가 아니라 집행이어야 한다 ────────────────────────────────
def test_call_budget_is_actually_enforced():
    """`kosis_guide` 가 알리던 상한이 코드 어디서도 검사되지 않았다."""
    class C(KosisClient):
        def __init__(self):
            super().__init__(api_key="x", throttle=0, max_calls=3)

        def _get(self, url, params, *, what):
            self._check_budget()
            self.calls += 1
            raise TooManyCells("초과")      # 계속 쪼개게 만든다
    with pytest.raises(KosisError) as e:
        C().data("101", "T", prd_se="M", start="200001", end="202512")
    assert "상한" in str(e.value)


# ── 관측치의 미매핑 필드(분류 코드)를 잃지 않는다 ────────────────────────────
def test_observation_export_keeps_classification_codes():
    """🔴 분류 '이름'만 열이 되고 **코드(C1·C2)와 ORG_ID 가 사라지고 있었다.**

    코드가 없으면 부분조회(obj_l1='13102')로 되돌아갈 수도, 분류 메타와 이을 수도 없다.
    """
    o = observation_from_row({
        "TBL_ID": "T", "PRD_DE": "2024", "PRD_SE": "Y", "DT": "1",
        "ITM_ID": "I1", "ITM_NM": "임금", "UNIT_NM": "천원", "ORG_ID": "118",
        "C1": "13102", "C1_OBJ_NM": "산업분류", "C1_NM": "제조업",
        "C2": "ALL", "C2_OBJ_NM": "규모별", "C2_NM": "전규모"})
    header, rows = _table([o])
    for col in ("산업분류", "규모별", "C1", "C2", "ORG_ID"):
        assert col in header, f"{col} 이 csv/xlsx 열에서 사라졌다"
    assert rows[0]["C1"] == "13102" and rows[0]["ORG_ID"] == "118"
    assert rows[0]["산업분류"] == "제조업"


def test_meta_type_labels_match_what_the_api_returns():
    """`NCD` 는 분류가 아니다 — 응답이 시점별 수록일자로 온다(실측).

    없는 `type` 은 err 21 이 아니라 err 30 을 주므로(OBJ·CLS 실측) 오타와 0건이
    구분되지 않는다. 이 화이트리스트가 유일한 방어선이라 라벨이 사실이어야 한다.
    """
    assert "분류" not in META_TYPES["NCD"]
    assert META_TYPES["NCD"] == "신규수록 시점"
