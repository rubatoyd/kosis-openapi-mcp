"""레코드 스키마 — 통계표와 관측치.

## 🔴 이 MCP 의 1차 산출물은 **통계 그 자체**다 (서지 형식이 아니다)

이 저장소는 통계를 쓰려는 사람에게 **표의 메타와 수치**를 준다. 그것이 본령이다.
서지·인용 형식은 **소비하는 쪽의 관심사**이지 이 API 의 모양이 아니다 —
서지 도구가 CSL 유형을 몇 개 아느냐에 따라 통계 응답의 스키마가 달라지면
그 도구 전용 어댑터가 되어 버린다.

그래서 층을 나눈다:

    [1차] Table(통계표 메타) · Observation(수치)   ← 도구 응답의 기본. 도메인 그대로.
    [부가] citation_fields()                       ← **선택적 투영** 하나. 부르는 쪽만 쓴다.

`citation_fields()` 는 편의이지 계약이 아니다. 서지 도구는 이것을 쓰거나, 1차
산출물을 보고 제 규칙대로 매핑하면 된다.

## 인용 단위: KOSIS 는 **두 층을 다 준다**

KOSIS 의 자료는 두 층으로 되어 있고, 응답에 **둘 다 실려 온다**:

    통계조사 (STAT_ID / STAT_NM)   예: 「인구총조사」        ← 발간물에 가까운 층
      └ 통계표 (TBL_ID / TBL_NM)   예: 「행정구역(시군구)별 성별 인구수」  ← 데이터셋 층

그래서 이 저장소는 **통계표 하나를 레코드 하나**로 내되, 레코드에 조사층 필드를
**함께 싣는다**. 인용하는 쪽이 어느 층으로 적을지 고르면 된다 — 우리가 미리 하나를
버리지 않는다.

- 조사층으로 인용하면(정기 간행물처럼) → `report` 로 담긴다.
- 표층으로 인용하면(데이터셋으로) → APA 의 데이터셋 규칙(작성기관, 연도, 제목
  [Data set], 배포처, URL)에 필요한 칸이 그대로 있다. 서지 도구에 `dataset` 유형이
  생기면 `citation_fields()` 의 `type` 만 바꾸면 된다.

⚠️ **관측치(수치 한 칸)는 문헌이 아니다.** 인용 단위는 표까지이고, 시점·분류는
   인용할 때의 위치다(「…인구수」, 2026년 8월 자료). 관측치는 `Observation` 으로
   따로 두고 서지 레코드로 만들지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── 정제 ────────────────────────────────────────────────────────────────────
_TAGS = re.compile(r"</?(?:br|p|div|span|b|i|strong|em|ul|li|table|tr|td)\b[^>]*>",
                   re.IGNORECASE)
_ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
        "&nbsp;": " ", "&ldquo;": "“", "&rdquo;": "”",
        "&lsquo;": "‘", "&rsquo;": "’"}


def clean_text(value: Any) -> str:
    """HTML 조각과 이중 이스케이프를 걷어낸다.

    ⚠️ 통계설명 본문에 `&amp;ldquo;` 처럼 **두 번 이스케이프된** 엔티티가 온다(실측).
       한 번만 풀면 `&ldquo;` 가 화면에 그대로 남는다.
    ⚠️ 알려진 태그 이름만 지운다 — 통계표명에 꺾쇠가 내용으로 쓰일 수 있다.
    """
    if value is None:
        return ""
    s = str(value)
    for _ in range(2):                     # 이중 이스케이프까지
        for k, v in _ENT.items():
            s = s.replace(k, v)
    s = _TAGS.sub(" ", s)
    s = s.replace("\xa0", " ")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def normalize_period(prd_de: str, prd_se: str = "") -> str:
    """수록시점을 사람이 읽는 꼴로. '202608'+M → '2026-08', '2026'+Y → '2026'.

    모르는 꼴은 원문 그대로 — 비우면 사라진다.
    """
    s = str(prd_de or "").strip()
    se = (prd_se or "").strip().upper()
    if not s.isdigit():
        return s
    if len(s) == 4:
        return s
    if len(s) == 6:
        if se == "Q":
            return f"{s[:4]}-Q{s[5]}" if s[4] == "0" else f"{s[:4]}-Q{s[4:]}"
        if se == "H":
            return f"{s[:4]}-H{s[5]}" if s[4] == "0" else f"{s[:4]}-H{s[4:]}"
        return f"{s[:4]}-{s[4:]}"
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


# ── 통계표(서지 레코드) ─────────────────────────────────────────────────────
COLUMNS = [
    "tbl_id", "tbl_nm", "org_id", "org_nm", "stat_id", "stat_nm",
    "vw_cd", "path", "prd_se", "period_from", "period_to", "updated", "url",
]


@dataclass
class Table:
    """통계표 하나 — **인용 가능한 단위**."""
    tbl_id: str = ""
    tbl_nm: str = ""
    org_id: str = ""
    org_nm: str = ""       # 작성기관 — 인용의 저자 자리
    stat_id: str = ""      # 통계조사 ID (발간물 층)
    stat_nm: str = ""      # 통계조사명 — 발간물로 인용할 때의 제목
    vw_cd: str = ""
    path: str = ""         # 분류 경로(인구 > 장래인구추계 > 전국) — 주제 맥락
    prd_se: str = ""       # 수록주기
    period_from: str = ""
    period_to: str = ""
    updated: str = ""
    url: str = ""
    keywords: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        return f"{self.org_id}:{self.tbl_id}" if self.tbl_id else self.tbl_nm

    def to_row(self) -> dict[str, str]:
        return {c: str(getattr(self, c, "") or "") for c in COLUMNS}

    def unmapped(self) -> dict[str, str]:
        """정규화 칸으로 안 간 원본 필드 — **버리지 않는다**(내보내기에 열로 나간다)."""
        used = {"TBL_ID", "TBL_NM", "ORG_ID", "ORG_NM", "STAT_ID", "STAT_NM",
                "VW_CD", "VW_NM", "MT_ATITLE", "FULL_PATH_ID", "PRD_SE",
                "STRT_PRD_DE", "END_PRD_DE", "SEND_DE", "LST_CHN_DE",
                "TBL_VIEW_URL", "LINK_URL"}
        return {k: clean_text(v) for k, v in self.raw.items() if k not in used}

    def scoring_text(self) -> str:
        """관련도 채점용 재료.

        ⚠️ 통계표에는 초록이 없다. 대신 **표제·조사명·분류 경로·기관명**을 합쳐 준다 —
           이것이 없으면 서지 도구의 '질문으로 찾기' 관문에서 조용히 걸러진다.
        """
        bits = [self.tbl_nm, self.stat_nm, self.path, self.org_nm,
                *(self.keywords or [])]
        return " · ".join(b for b in dict.fromkeys(bits) if b)

    def citation_fields(self) -> dict[str, Any]:
        """**선택적** 서지 투영 — 이 MCP 의 1차 산출물이 아니다.

        통계를 쓰려는 사람에게는 `to_row()`(표 메타)와 `Observation`(수치)이 본령이고,
        이것은 서지 도구로 넘기고 싶을 때만 부르는 부가 기능이다.


        `type` 은 오늘 **report**(조사=발간물 층)로 낸다. 데이터셋 유형이 생기면
        `dataset` 으로 바꾸면 되고, 그때 필요한 칸(작성기관·연도·제목·배포처·URL)은
        이미 여기 다 있다. `unit` 이 어느 층으로 적었는지 알려 준다.
        """
        year = (self.period_to or self.updated or "")[:4]
        return {
            "type": "report",
            "unit": "table",
            "title": self.tbl_nm,
            "container_title": self.stat_nm,      # 조사명(발간물 층)
            "authority": self.org_nm,             # 작성기관 = 저자 자리
            "publisher": self.org_nm,
            "issued": year,
            "period": " ~ ".join(x for x in (self.period_from, self.period_to) if x),
            "accessed": "",                       # 호출자가 조회일자를 채운다
            "identifier": f"KOSIS {self.org_id}/{self.tbl_id}",
            "url": self.url,
            "note": "통계표 단위. 데이터셋으로 인용할 경우 제목 뒤에 [Data set] 을 붙이고 "
                    "배포처를 KOSIS 로 적는다.",
        }


_URL_TBL = ("https://kosis.kr/statHtml/statHtml.do?orgId={org}&tblId={tbl}")


def table_from_row(r: dict, *, vw_cd: str = "") -> Table:
    """검색·목록 응답의 한 행 → Table.

    ⚠️ 서비스마다 키 집합이 다르다(통계목록은 LIST_*, 통합검색은 TBL_*·MT_ATITLE…).
       **있는 것만 취하고 나머지는 raw 에 남긴다.**
    """
    def v(*names: str) -> str:
        for n in names:
            if r.get(n):
                return clean_text(r[n])
        return ""

    org = v("ORG_ID")
    tbl = v("TBL_ID")
    url = v("TBL_VIEW_URL", "LINK_URL")
    if not url and org and tbl:
        url = _URL_TBL.format(org=org, tbl=tbl)
    prd_se = v("PRD_SE")
    return Table(
        tbl_id=tbl,
        tbl_nm=v("TBL_NM", "LIST_NM"),
        org_id=org,
        org_nm=v("ORG_NM"),
        stat_id=v("STAT_ID"),
        stat_nm=v("STAT_NM"),
        vw_cd=v("VW_CD") or vw_cd,
        path=v("MT_ATITLE", "VW_NM"),
        prd_se=prd_se,
        period_from=normalize_period(v("STRT_PRD_DE"), prd_se),
        period_to=normalize_period(v("END_PRD_DE"), prd_se),
        updated=normalize_period(v("SEND_DE", "LST_CHN_DE")),
        url=url,
        raw=dict(r),
    )


# ── 관측치 ──────────────────────────────────────────────────────────────────
OBS_COLUMNS = ["tbl_id", "tbl_nm", "period", "prd_se", "item", "item_id",
               "unit", "value", "updated"]


@dataclass
class Observation:
    """수치 한 칸. **문헌이 아니다** — 인용 단위는 표까지다."""
    tbl_id: str = ""
    tbl_nm: str = ""
    period: str = ""
    prd_se: str = ""
    item: str = ""
    item_id: str = ""
    unit: str = ""
    value: str = ""
    updated: str = ""
    classes: dict[str, str] = field(default_factory=dict)   # C1_OBJ_NM → C1_NM
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, str]:
        row = {c: str(getattr(self, c, "") or "") for c in OBS_COLUMNS}
        row.update(self.classes)
        return row

    def unmapped(self) -> dict[str, str]:
        """정규화 칸으로 안 간 원본 필드 — **버리지 않는다**.

        🔴 여기가 비어 있어서 csv·xlsx 가 **분류 코드(C1·C2)와 ORG_ID 를 통째로
           잃고 있었다.** 분류는 이름만 열이 되고 코드는 어디에도 없었는데, 코드가
           없으면 부분조회(`obj_l1='13102'`)로 되돌아갈 수도, 분류 메타와 이을 수도
           없다. 값이 있는데 볼 방법이 없는 것은 조용한 데이터 손실이다
           (자매 저장소 na-openapi-mcp 가 같은 자리에서 서술형 본문을 잃었다).
        """
        used = {"TBL_ID", "TBL_NM", "PRD_DE", "PRD_SE", "ITM_ID", "ITM_NM",
                "UNIT_NM", "DT", "LST_CHN_DE"}
        used.update(f"C{i}_{suffix}" for i in range(1, 9)
                    for suffix in ("NM", "OBJ_NM"))
        return {k: clean_text(v) for k, v in self.raw.items() if k not in used}


def observation_from_row(r: dict) -> Observation:
    """통계자료 응답의 한 행 → Observation.

    분류는 `C1..C8` 이 값 ID, `C1_NM..` 이 값 이름, `C1_OBJ_NM..` 이 분류 이름이다.
    **분류 개수가 표마다 다르므로** 있는 것만 훑는다(2~8은 없으면 생략된다 — 문서 명시).
    """
    def c(k: str) -> str:
        return clean_text(r.get(k))

    classes: dict[str, str] = {}
    for i in range(1, 9):
        obj = c(f"C{i}_OBJ_NM")
        nm = c(f"C{i}_NM")
        if obj or nm:
            classes[obj or f"분류{i}"] = nm
    return Observation(
        tbl_id=c("TBL_ID"), tbl_nm=c("TBL_NM"),
        period=normalize_period(c("PRD_DE"), c("PRD_SE")),
        prd_se=c("PRD_SE"),
        item=c("ITM_NM"), item_id=c("ITM_ID"),
        unit=c("UNIT_NM"), value=c("DT"),
        updated=normalize_period(c("LST_CHN_DE")),
        classes=classes, raw=dict(r),
    )
