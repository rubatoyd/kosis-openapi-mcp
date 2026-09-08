"""명령줄 인터페이스 — MCP 서버와 같은 코어를 쓴다.

    kosis status
    kosis guide
    kosis search 사교육비
    kosis list --vw MT_ZTITLE
    kosis meta --org 101 --tbl DT_1B040A3 --kind ITM
    kosis data --org 101 --tbl DT_1B040A3 --prd M --start 202401 --end 202412
    kosis collect --org 101 --tbl DT_1B040A3 --prd M --start 202101 --end 202512
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .client import KosisClient, KosisError
from .config import ERROR_CODES, META_TYPES, PERIODS, VIEW_CODES, get_api_key
from .exporters import export


def use_utf8_stdio() -> None:
    """🔴 Windows 콘솔(cp949)에서 출력이 통째로 죽는 것을 막는다.

    이 CLI 의 출력문에는 `—`·`·`·`📁` 가 섞여 있는데, 파이썬은 Windows 에서 stdout
    인코딩을 **ANSI 코드페이지(한국어 = cp949)** 로 잡는다. 그래서 `kosis guide`
    같이 API 를 부르지도 않는 명령까지 이렇게 죽었다(2026-09-08 실측):

        오류: 'cp949' codec can't encode character '\\u2014'

    ⚠️ 게다가 `UnicodeEncodeError` 는 `ValueError` 의 하위라 아래 `except` 에 걸려
       **인코딩 사고가 API 오류로 둔갑**했다. 원인이 가려지는 쪽이 더 나쁘다.
    ⚠️ CI 는 ubuntu 에서만 도는 탓에 이것을 못 잡았다 — 회귀로 고정한다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 파이프·캡처 등 재설정할 수 없는 스트림이면 그대로 둔다


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_status(args) -> int:
    info = KosisClient().status()
    _dump(info)
    return 0 if info.get("ok") else 1


def cmd_guide(args) -> int:
    print("[서비스뷰]")
    for k, v in VIEW_CODES.items():
        print(f"  {k:<18} {v}")
    print("\n[수록주기]")
    print("  " + " · ".join(f"{k}={v}" for k, v in PERIODS.items()))
    print("\n[메타 종류]")
    print("  " + " · ".join(f"{k}={v}" for k, v in META_TYPES.items()))
    print("\n[오류코드]")
    for k, v in ERROR_CODES.items():
        print(f"  {k}  {v.splitlines()[0][:76]}")
    print("\n[한계] 요청당 4만 셀 · 분당 200건 · 통합검색/목록에 페이징 없음")
    return 0


def cmd_search(args) -> int:
    tables, meta = KosisClient().search(args.query, max_records=args.max_records)
    if args.json:
        _dump({"tables": [t.to_row() for t in tables], "meta": meta})
        return 0
    print(f"'{meta['query']}' — {meta.get('total', 0):,}건 중 {len(tables):,}건")
    for t in tables:
        print(f"  {t.org_id}/{t.tbl_id:<16} {t.tbl_nm[:56]}")
        tail = " · ".join(x for x in (t.org_nm, t.stat_nm,
                                      f"{t.period_from}~{t.period_to}") if x.strip(" ~"))
        if tail:
            print(f"  {'':<17} {tail}")
    for k in ("truncated_note", "note"):
        if meta.get(k):
            print(f"\n  [!] {meta[k]}")
    return 0


def cmd_list(args) -> int:
    tables, meta = KosisClient().list_tree(args.vw, args.parent or None)
    if args.json:
        _dump({"items": [t.to_row() for t in tables], "meta": meta})
        return 0
    print(f"[{meta['vw_nm']}] {meta['parent_id']} — "
          f"폴더 {meta.get('folders', 0)} · 통계표 {meta.get('tables', 0)}")
    for t in tables:
        mark = "📁" if not t.tbl_id else "  "
        ident = t.raw.get("LIST_ID", "") if not t.tbl_id else f"{t.org_id}/{t.tbl_id}"
        print(f"  {mark} {ident:<22} {t.tbl_nm[:52]}")
    if meta.get("note"):
        print(f"\n  [!] {meta['note']}")
    return 0


def cmd_meta(args) -> int:
    rows, meta = KosisClient().meta(args.org, args.tbl, args.kind)
    if args.json:
        _dump({"rows": rows, "meta": meta})
        return 0
    print(f"[{meta['label']}] {meta['total']}건")
    for r in rows[:args.limit]:
        print("  " + json.dumps(r, ensure_ascii=False)[:150])
    if meta["total"] > args.limit:
        print(f"  … ({meta['total']}건 중 {args.limit}건 표시)")
    return 0


def cmd_explain(args) -> int:
    rows, meta = KosisClient().explain(args.org, args.tbl)
    if args.json:
        _dump({"rows": rows, "meta": meta})
        return 0
    if meta.get("no_data"):
        print(meta.get("note", "설명자료 없음"))
        return 0
    for r in rows:
        for k, v in r.items():
            print(f"[{k}]\n{str(v)[:1200]}\n")
    return 0


def _data(args):
    levels = {f"objL{i}": v.strip()
              for i, v in enumerate(args.obj2 or [], start=2) if v.strip()}
    return KosisClient().data(
        args.org, args.tbl, prd_se=args.prd,
        start=args.start or None, end=args.end or None,
        recent=args.recent or None, obj_l1=args.obj, obj_levels=levels,
        items=args.items, max_rows=10 ** 9)


def cmd_data(args) -> int:
    obs, meta = _data(args)
    if args.json:
        _dump({"observations": [o.to_row() for o in obs[:args.limit]], "meta": meta})
        return 0
    print(f"{meta['total']:,}행 · 요청 {meta['requests']}회 "
          f"· 분류축 {' '.join(meta.get('obj_levels') or {})}")
    for k in ("obj_note", "split_note"):
        if meta.get(k):
            print(f"  [!] {meta[k]}")
    for o in obs[:args.limit]:
        cls = " ".join(f"{v}" for v in o.classes.values())
        print(f"  {o.period:<10} {cls[:28]:<28} {o.item[:14]:<14} {o.value:>14} {o.unit}")
    if meta["total"] > args.limit:
        print(f"  … ({meta['total']:,}행 중 {args.limit}행 표시 — 전부 받으려면 collect)")
    return 0


def cmd_collect(args) -> int:
    obs, meta = _data(args)
    paths = export(obs, [f.strip() for f in args.format.split(",") if f.strip()],
                   args.out_dir, args.name, kind="observation")
    print(f"{len(obs):,}행 수집 · API 호출 {meta['requests']}회")
    for p in paths:
        print(f"  저장: {p}")
    for k in ("obj_note", "split_note"):
        if meta.get(k):
            print(f"  [!] {meta[k]}")
    return 0


def cmd_indicator(args) -> int:
    inds, meta = KosisClient().indicator_search(
        name=args.name, jipyo_id=args.id, max_records=args.max_records)
    if args.json:
        _dump({"indicators": [i.to_row() for i in inds], "meta": meta})
        return 0
    print(f"'{meta['query']}' — {meta.get('total', 0):,}건 "
          f"(페이지 {meta.get('pages', 0)}쪽 회수)")
    for i in inds:
        print(f"  {i.jipyo_id:<8} {i.jipyo_nm[:40]:<40} {i.unit:<8} "
              f"{i.period_from}~{i.period_to} ({i.periods}시점) {i.area}")
    for k in ("truncated_note", "note"):
        if meta.get(k):
            print(f"\n  [!] {meta[k]}")
    return 0


def cmd_indicator_data(args) -> int:
    vals, meta = KosisClient().indicator_data(
        args.id, start=args.start, end=args.end, recent=args.recent)
    if args.json:
        _dump({"values": [v.to_row() for v in vals], "meta": meta})
        return 0
    print(f"{meta['total']:,}건 · 지표 전체 {meta.get('available', 0):,}시점 "
          f"· 페이지 {meta.get('pages', 0)}쪽")
    if not meta.get("server_filtered", True):
        print(f"  [!] {meta['filter_note']}")
    for v in vals[:args.limit]:
        print(f"  {v.period:<10} {v.item[:16]:<16} {v.value:>14}")
    if meta["total"] > args.limit:
        print(f"  … ({meta['total']:,}건 중 {args.limit}건 표시)")
    if meta.get("note"):
        print(f"  [!] {meta['note']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kosis", description="KOSIS 공유서비스 CLI")
    p.add_argument("--version", action="version",
                   version=f"kosis-openapi-mcp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="연결 점검").set_defaults(func=cmd_status)
    sub.add_parser("guide", help="서비스뷰·주기·오류코드·한계").set_defaults(func=cmd_guide)

    s = sub.add_parser("search", help="통계표 찾기")
    s.add_argument("query")
    s.add_argument("--max-records", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    l = sub.add_parser("list", help="통계목록 트리")
    l.add_argument("--vw", default="MT_ZTITLE")
    l.add_argument("--parent", default="", help="비우면 최상위")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    m = sub.add_parser("meta", help="통계표 메타자료")
    m.add_argument("--org", required=True)
    m.add_argument("--tbl", required=True)
    m.add_argument("--kind", default="TBL", help=" · ".join(META_TYPES))
    m.add_argument("--limit", type=int, default=10)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_meta)

    e = sub.add_parser("explain", help="통계설명(조사개요)")
    e.add_argument("--org", required=True)
    e.add_argument("--tbl", required=True)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_explain)

    def data_args(sp):
        sp.add_argument("--org", required=True)
        sp.add_argument("--tbl", required=True)
        sp.add_argument("--prd", required=True, help="Y·H·Q·M·D")
        sp.add_argument("--start", default="")
        sp.add_argument("--end", default="")
        sp.add_argument("--recent", type=int, default=0)
        sp.add_argument("--obj", default="ALL", help="분류1 — ALL·11·11*·11+21")
        # 🔴 다축 표(예: 산업 × 규모)를 위한 자리. 비워 두면 클라이언트가 err 20(objL)을
        #    보고 필요한 만큼 ALL 로 자동으로 채운다 — 보통은 줄 필요가 없다.
        sp.add_argument("--obj2", action="append", metavar="코드",
                        help="분류2 이후를 순서대로. 반복하면 objL2, objL3 … "
                             "(생략하면 자동으로 맞춘다)")
        sp.add_argument("--items", default="ALL")

    d = sub.add_parser("data", help="통계표 수치")
    data_args(d)
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_data)

    # ── 통계주요지표 (규칙이 다른 계열 — docs §7) ────────────────────────────
    i = sub.add_parser("indicator", help="주요지표 찾기(통계표와 다른 계열)")
    i.add_argument("name", nargs="?", default="", help="지표명(예: 출산율)")
    i.add_argument("--id", default="", help="지표ID 로 직접 찾기")
    i.add_argument("--max-records", type=int, default=30)
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_indicator)

    iv = sub.add_parser("indicator-data", help="주요지표의 시점별 수치")
    iv.add_argument("--id", required=True, help="지표ID (indicator 로 찾는다)")
    iv.add_argument("--start", default="")
    iv.add_argument("--end", default="")
    iv.add_argument("--recent", type=int, default=0)
    iv.add_argument("--limit", type=int, default=20)
    iv.add_argument("--json", action="store_true")
    iv.set_defaults(func=cmd_indicator_data)

    c = sub.add_parser("collect", help="수치를 파일로 저장")
    data_args(c)
    c.add_argument("--out-dir", default="output")
    c.add_argument("--name", default="kosis")
    c.add_argument("--format", default="xlsx,json")
    c.set_defaults(func=cmd_collect)
    return p


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdio()
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.cmd != "guide" and not get_api_key():
        print("KOSIS_API_KEY 미설정 — kosis.kr 회원가입 후 공유서비스 활용신청에서 받은 "
              "인증키를 .env 또는 환경변수로 주세요. 발급된 값을 그대로 쓰세요"
              "(base64 처럼 보여도 디코드 금지).", file=sys.stderr)
        return 1
    try:
        return args.func(args)
    except (KosisError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
