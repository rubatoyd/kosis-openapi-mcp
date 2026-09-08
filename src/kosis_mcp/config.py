"""환경설정·엔드포인트·서비스 레지스트리 — KOSIS 공유서비스(OpenAPI).

규격의 출처는 국가데이터처 **KOSIS 공유서비스 개발가이드**(172쪽)이고, 이 파일의
값은 그 위에 **라이브 실측**을 얹어 확정한다. 근거는 docs/KOSIS_API_GUIDE.md,
재현은 `scripts/probe_api.py`.

🔴 **인증키를 디코드하지 말 것.** KOSIS 인증키는 base64 처럼 생겼고(끝이 `=`)
   실제로 base64 이지만, **발급된 문자열 그대로** 보내야 한다. 풀어서 보내면
   `{"err":"11","errMsg":"유효하지않은 인증KEY입니다."}` 가 온다(✅ 실측).
   개발가이드의 예제 URL 도 인코딩된 형태를 그대로 싣고 있다.
"""
from __future__ import annotations

import logging
import os
import re

from dotenv import load_dotenv

load_dotenv(override=False)

BASE = os.environ.get("KOSIS_BASE_URL", "https://kosis.kr/openapi")

# ⚠️ 통계설명 상세(StatsExplain)만 경로의 대소문자가 다르다 — `openApi` 다(개발가이드 p129).
#    `openapi` 로 고쳐 쓰면 안 된다. 서버가 경로를 구분한다.
ENDPOINTS = {
    # 🔴 **통계자료의 '통계표선택(파라미터) 방식'은 별도 경로다** — `/openapi/Param/`.
    #    개발가이드(172쪽)는 입력 변수 표를 `statisticsData.do` 아래에 싣지만, 그 URL 로
    #    orgId/tblId 를 보내면 **무조건 err 20(필수요청변수 누락)** 이 온다(실측).
    #    가이드의 예제 URL 은 전부 `userStatsId`(자료등록 방식)뿐이라 이 경로가 문서 어디에도
    #    나오지 않는다. 여기서 반나절이 갈린다.
    "param":   f"{BASE}/Param/statisticsParameterData.do",   # 통계자료(파라미터 방식)
    "list":    f"{BASE}/statisticsList.do",      # 통계목록
    "data":    f"{BASE}/statisticsData.do",      # 통계자료 (+ method=getMeta 로 메타자료)
    "meta":    f"{BASE}/statisticsData.do",      # 메타자료 — 같은 URL, method=getMeta
    "bigdata": f"{BASE}/statisticsBigData.do",   # 대용량 통계자료
    "expl":    f"{BASE}/statisticsExplData.do",  # 통계설명
    "explain": "https://kosis.kr/openApi/StatsExplain.do",   # ⚠️ openApi (대문자 A)
    "search":  f"{BASE}/statisticsSearch.do",    # KOSIS 통합검색
}

# 통계목록의 서비스뷰 코드 (개발가이드 p19). 📄 문서 기준 — 실측으로 확인한 것은 표시한다.
VIEW_CODES = {
    "MT_ZTITLE": "국내통계 주제별",
    "MT_OTITLE": "국내통계 기관별",
    "MT_GTITLE01": "e-지방지표(주제별)",
    "MT_GTITLE02": "e-지방지표(지역별)",
    "MT_GTITLE03": "e-지방지표(테마별)",
    "MT_CHOSUN_TITLE": "광복이전통계(1908~1943)",
    "MT_HANKUK_TITLE": "대한민국통계연감",
    "MT_STOP_TITLE": "작성중지통계",
    "MT_RTITLE": "국제통계",
    "MT_RTITLE01": "국제지역통계",
    "MT_BUKHAN": "북한통계",
    "MT_TM1_TITLE": "대상별통계",
    "MT_TM2_TITLE": "이슈별통계",
    "MT_ETITLE": "영문 KOSIS",
}

# 메타자료 종류 (개발가이드 p152~160 · ✅ 2026-09-08 9종 전수 실측)
#
# ⚠️ **없는 `type` 은 err 21 이 아니라 err 30(자료 없음)을 준다**(실측: OBJ·CLS).
#    오타와 '정말 0건'이 구분되지 않으므로 이 화이트리스트가 유일한 방어선이다 —
#    라벨이 틀리면 그 방어선이 사람을 엉뚱한 곳으로 보낸다.
#
# 🔴 `NCD` 는 **분류가 아니다.** 응답이 시점별 수록일자(PRD_DE·SEND_DE)로 오고
#    분류 코드는 한 칸도 없다. 분류축을 알려 주는 메타 서비스는 **존재하지 않는다** —
#    그래서 client 가 err 20 `(objL)` 을 보고 축 개수를 맞혀 나간다.
META_TYPES = {
    "TBL": "통계표", "PRD": "수록기간", "CMMT": "주석", "SOURCE": "출처",
    "NCD": "신규수록 시점", "ORG": "기관", "ITM": "항목",
    "UNIT": "단위", "WGT": "가중치",
}

# 수록주기 코드
PERIODS = {"Y": "년", "H": "반기", "Q": "분기", "M": "월", "D": "일", "IR": "부정기"}

# ── 오류코드 (개발가이드 p16 · ✅ 10·11·20·21·30·31 은 라이브 관측) ──────────
# ⚠️ 문서와 실제 문구가 다른 것이 있다 — 11 을 문서는 '인증키 기간만료'라 적지만
#    실제 응답은 "유효하지않은 인증KEY입니다" 다(잘못된 키에도 11이 온다).
ERROR_CODES = {
    "10": "인증키 누락 — KOSIS_API_KEY 를 설정하세요.",
    "11": "인증키가 유효하지 않거나 기간이 만료됐습니다. "
          "🔴 발급된 값을 **그대로** 쓰고 있는지 확인하세요(base64 처럼 보여도 디코드 금지).",
    "20": "필수요청변수 누락 — 원인이 둘입니다(실측). "
          "① 통계자료를 orgId/tblId 로 부를 때는 "
          "`/openapi/Param/statisticsParameterData.do` 를 써야 합니다 — "
          "`statisticsData.do` 로 보내면 항상 이 오류입니다. "
          "② 메시지에 `(objL)` 이 붙었다면 **분류축을 덜 보낸 것**입니다 — "
          "표의 축 개수만큼 objL1~objL8 을 채워야 합니다(kosis_data 가 자동으로 맞춥니다).",
    "21": "잘못된 요청변수 — 값의 형식이나 존재 여부를 확인하세요. "
          "🔴 분류축을 **너무 많이** 보내도 이 오류입니다 — 축 개수는 모자라도(err 20) "
          "넘쳐도(err 21) 안 되고, 표의 실제 축 수와 정확히 맞아야 합니다(실측).",
    "30": "조회결과 없음 — 오류가 아니라 '해당 조건에 자료가 없다'는 사실입니다.",
    "31": "조회결과 초과(4만 셀 제한) — 시점이나 분류를 쪼개서 부르세요.",
    "40": "분당 호출 한도(200건) 초과 — 잠시 뒤 다시 시도하세요.",
    "41": "호출 가능 ROW 수 제한 — 조회 범위를 줄이세요.",
    "42": "사용자별 이용 제한 — 관리자에게 문의.",
    "50": "서버 오류 — 잠시 뒤 다시 시도하세요.",
}

# 🔴 시스템 부하 제한: **한 번의 통계자료 요청은 4만 셀 이하**(개발가이드 p33).
#    넘으면 err 31 이 온다. 실측: 같은 표에서 12개월치 10,590행은 정상,
#    60개월치는 err 31. 클라이언트가 시점을 쪼개 자동으로 다시 부른다.
MAX_CELLS_PER_REQUEST = int(os.environ.get("KOSIS_MAX_CELLS", "40000"))

# 분당 호출 한도 200건 → 안전하게 0.35초 간격(≈171/분)을 기본으로 둔다.
DEFAULT_THROTTLE = float(os.environ.get("KOSIS_THROTTLE", "0.35"))
MAX_CALLS_PER_TOOL_CALL = int(os.environ.get("KOSIS_MAX_CALLS_PER_TOOL_CALL", "120"))

# 🔴 분류축(objL)은 **표마다 개수가 다르고, 요청의 개수가 정확히 맞아야 한다**(실측).
#    모자라면 err 20 "(objL)", 넘치면 err 21 — '전부 보내기'로는 못 넘긴다.
#    축 개수를 알려 주는 메타 서비스가 없어 클라이언트가 하나씩 늘려 가며 찾는다.
MAX_OBJ_LEVELS = 8


def get_api_key() -> str | None:
    """인증키 조회.

    🔴 **가공하지 않는다** — strip 만 한다. base64 로 보인다고 디코드하면 인증이 깨진다.
    """
    for name in ("KOSIS_API_KEY", "KOSIS_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return None


# 인증키가 오류 메시지·응답 에코를 타고 새는 것을 막는다.
# KOSIS 는 요청 URL 을 에코하지 않는 것으로 관측됐지만, 확정 전까지 막아두는 쪽이 옳다.
_KEY_PARAM = re.compile(r"(?i)\b(apiKey|apikey|key)=[^&\s\"'<>]+")


def scrub(text: str) -> str:
    """자격증명형 파라미터를 지운다. 인증키가 로그·도구 응답에 남지 않게."""
    s = _KEY_PARAM.sub(lambda m: m.group(1) + "=***", str(text or ""))
    key = get_api_key()
    if key and key in s:          # URL 밖에 맨몸으로 실린 경우까지
        s = s.replace(key, "***")
    return s


class _ScrubFilter(logging.Filter):
    """로그 인자에서 인증키를 지운다.

    ⚠️ **문자열만 건드린다.** 모든 인자를 `str()` 로 바꾸면 `%d` 포맷이 터진다
       (자매 저장소 law-openapi-mcp 에서 실제로 겪고 회귀로 고정한 결함이다).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(
                    scrub(a) if isinstance(a, str) else a for a in record.args)
        except Exception:  # noqa: BLE001
            pass
        return True


_scrubber_installed = False


def install_log_scrubber() -> None:
    global _scrubber_installed
    if _scrubber_installed:
        return
    for name in ("urllib3", "urllib3.connectionpool", "requests", "kosis_mcp"):
        logging.getLogger(name).addFilter(_ScrubFilter())
    _scrubber_installed = True


_trust_done = False


def use_os_trust() -> None:
    """교육망·사내망 SSL 인터셉션 대응 — OS 신뢰저장소 사용(검증은 유지).

    ⚠️ 등록 명령줄이 아니라 **코드에서** 부른다 — .mcpb/바이너리 경로에는 옵션이 닿지 않는다.
    """
    global _trust_done
    if _trust_done or (os.environ.get("KOSIS_OS_TRUST", "1").strip() == "0"):
        return
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("kosis_mcp").debug("truststore 미적용: %s", type(e).__name__)
    _trust_done = True
