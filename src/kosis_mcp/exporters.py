"""수집 결과를 xlsx/csv/json/sqlite 로 저장.

관측치(Observation)는 분류 축이 표마다 달라 **열 구성이 가변**이다 —
분류 이름을 그대로 열로 쓴다(행정구역(시군구)별, 성별 …).

🔴 **미매핑 필드를 열로 승격한다.** 자매 저장소(na-openapi-mcp)의 적대적 검토가
   잡은 결함이다 — 정규화 표에 없는 필드가 MCP 응답에도, csv 에도, xlsx 에도
   나오지 않아 국회의안의 '제안이유 및 주요내용'(97% 채워진 서술형 본문)을
   통째로 잃었다. 값이 있는데 사용자가 볼 방법이 없는 것은 조용한 데이터 손실이다.

   여기서는 정규화 열 뒤에 **실제로 값이 있는 원본 필드를 전부** 붙인다.
   json·sqlite 는 `raw` 를 통째로 싣는다.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Sequence

from .models import COLUMNS, OBS_COLUMNS, Observation, Table

_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, *, fallback: str = "kosis_output", limit: int = 60) -> str:
    """파일명으로 안전한 문자열 — **디렉터리를 벗어날 수 없게** 만든다.

    ⚠️ 검색어가 그대로 파일명이 되는 경로가 있어 사용자 입력이 경로에 닿는다.
       이 함수가 없으면 `name="../escaped"` 가 out_dir **밖에** 파일을 쓴다
       (자매 저장소 세 곳에 모두 있던 결함이다).
    """
    s = _UNSAFE.sub("_", str(name or ""))
    s = s.replace("..", "_").strip().strip(". ")
    s = re.sub(r"\s+", "_", s)[:limit].strip("._ ")
    if not s or s.upper().split(".")[0] in _RESERVED:
        s = fallback
    return s


def extra_columns(records: Sequence) -> list[str]:
    """정규화 열에 안 담긴 원본 필드 중 **값이 하나라도 있는** 것들.

    값이 전부 빈 필드까지 열로 만들면 표가 넓어지기만 한다. 다만 '없는 필드'와
    '빈 필드'의 구분이 필요하면 json 의 `raw` 를 보면 된다 — 거기엔 다 있다.
    """
    seen: dict[str, None] = {}
    for r in records:
        if not hasattr(r, "unmapped"):
            continue
        for k, v in r.unmapped().items():
            if v:
                seen.setdefault(k, None)
    return list(seen)


def _table(records: Sequence) -> tuple[list[str], list[dict]]:
    """열 머리와 행들. 관측치와 통계표를 둘 다 받는다.

    ⚠️ 관측치는 **분류 축이 표마다 다르다** — 고정 열 뒤에 실제로 나온 분류 이름을
       열로 붙인다. 고정 스키마를 강요하면 분류가 통째로 사라진다.
    """
    if records and isinstance(records[0], Observation):
        seen: dict[str, None] = {}
        for r in records:
            for k in r.classes:
                seen.setdefault(k, None)
        base = list(OBS_COLUMNS) + list(seen)
    else:
        base = list(COLUMNS)

    # 🔴 관측치도 미매핑 필드를 승격한다 — 여기가 빠져 있어서 분류 코드(C1·C2)와
    #    ORG_ID 가 csv·xlsx 에서 사라지고 있었다. json·sqlite 만 raw 로 살아남았다.
    extras = extra_columns(records)
    header = base + [k for k in extras if k not in base]
    rows = []
    for r in records:
        row = r.to_row()
        um = r.unmapped()
        for k in extras:
            row.setdefault(k, um.get(k, ""))
        rows.append(row)
    return header, rows


def to_json(records: Sequence, path: str) -> None:
    """정규화 행 + 원본 필드(raw) + (통계표라면) 서지 매핑을 함께 저장."""
    data = []
    for r in records:
        item = {**r.to_row(), "raw": r.raw}
        if isinstance(r, Table):
            item["citation"] = r.citation_fields()
            item["scoring_text"] = r.scoring_text()
        data.append(item)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def to_csv(records: Sequence, path: str) -> None:
    header, rows = _table(records)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # 엑셀 한글 호환 BOM
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def to_xlsx(records: Sequence, path: str) -> None:
    from openpyxl import Workbook

    header, rows = _table(records)
    wb = Workbook()
    ws = wb.active
    ws.title = "records"
    ws.append(header)
    for row in rows:
        # 엑셀 셀 상한은 32,767자다. 넘으면 openpyxl 이 예외를 내며 **파일 전체가
        # 안 써진다** — 조문 본문이 그 길이를 쉽게 넘으므로 잘라서 표시하고 표시한다.
        ws.append([_cell(row.get(c, "")) for c in header])
    wb.save(path)


_XLSX_CELL_LIMIT = 32_767


def _cell(value: str) -> str:
    s = str(value or "")
    if len(s) <= _XLSX_CELL_LIMIT:
        return s
    keep = _XLSX_CELL_LIMIT - 40
    return s[:keep] + f"…[{len(s):,}자 중 잘림 — 전문은 json 참조]"


def to_sqlite(records: Sequence, path: str, *, table: str = "records") -> None:
    header, rows = _table(records)
    con = sqlite3.connect(path)
    try:
        cols = ", ".join(f'"{c}" TEXT' for c in header)
        con.execute(f"DROP TABLE IF EXISTS {table}")   # 스냅샷: 재실행 시 누적 방지
        con.execute(f'CREATE TABLE {table} ({cols}, "raw" TEXT)')
        ph = ", ".join(["?"] * (len(header) + 1))
        names = ", ".join(f'"{c}"' for c in header)
        for r, row in zip(records, rows):
            con.execute(
                f'INSERT INTO {table} ({names}, "raw") VALUES ({ph})',
                [row.get(c, "") for c in header]
                + [json.dumps(r.raw, ensure_ascii=False)])
        con.commit()
    finally:
        con.close()


_EXPORTERS = {"json": to_json, "csv": to_csv, "xlsx": to_xlsx, "sqlite": to_sqlite}
_EXT = {"json": ".json", "csv": ".csv", "xlsx": ".xlsx", "sqlite": ".sqlite"}


def export(records: Sequence, formats: Sequence[str] | str, out_dir: str,
           name: str) -> list[str]:
    """formats 각각으로 out_dir/name.* 저장. 저장된 경로 목록 반환.

    🔴 **쓰기 전에 형식을 전부 검증한다.** 쓰기 루프 안에서 검증하면
       `['json','bogus']` 가 json 을 쓴 뒤 예외를 내 — 수집 메타가 통째로 사라지고
       쿼터는 이미 쓴 뒤다(자매 저장소 적대적 검토 실측).
    """
    if isinstance(formats, str):
        # 문자열을 넘기면 문자 단위로 순회해 '지원하지 않는 형식: j' 가 났다.
        formats = [formats]
    keys: list[str] = []
    for fmt in formats:
        key = str(fmt).lower().lstrip(".")
        if key == "db":
            key = "sqlite"
        if key not in _EXPORTERS:
            raise ValueError(
                f"지원하지 않는 출력형식: {fmt!r} (가능: {list(_EXPORTERS)}). "
                f"아무 파일도 쓰지 않았습니다.")
        keys.append(key)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out.resolve()
    stem = safe_name(name)
    paths: list[str] = []
    for key in keys:
        p = (out / f"{stem}{_EXT[key]}").resolve()
        if base != p.parent:      # 정규화를 뚫는 경로가 남아 있으면 멈춘다(이중 방어)
            raise ValueError(f"출력 경로가 지정 디렉터리를 벗어납니다: {p}")
        _EXPORTERS[key](records, str(p))
        paths.append(str(p))
    return paths
