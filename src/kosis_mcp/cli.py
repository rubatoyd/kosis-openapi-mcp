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
    return KosisClient().data(
        args.org, args.tbl, prd_se=args.prd,
        start=args.start or None, end=args.end or None,
        recent=args.recent or None, obj_l1=args.obj, items=args.items,
        max_rows=10 ** 9)


def cmd_data(args) -> int:
    obs, meta = _data(args)
    if args.json:
        _dump({"observations": [o.to_row() for o in obs[:args.limit]], "meta": meta})
        return 0
    print(f"{meta['total']:,}행 · 요청 {meta['requests']}회")
    if meta.get("split_note"):
        print(f"  [!] {meta['split_note']}")
    for o in obs[:args.limit]:
        cls = " ".join(f"{v}" for v in o.classes.values())
        print(f"  {o.period:<10} {cls[:28]:<28} {o.item[:14]:<14} {o.value:>14} {o.unit}")
    if meta["total"] > args.limit:
        print(f"  … ({meta['total']:,}행 중 {args.limit}행 표시 — 전부 받으려면 collect)")
    return 0


def cmd_collect(args) -> int:
    obs, meta = _data(args)
    paths = export(obs, [f.strip() for f in args.format.split(",") if f.strip()],
                   args.out_dir, args.name)
    print(f"{len(obs):,}행 수집 · API 호출 {meta['requests']}회")
    for p in paths:
        print(f"  저장: {p}")
    if meta.get("split_note"):
        print(f"  [!] {meta['split_note']}")
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
        sp.add_argument("--items", default="ALL")

    d = sub.add_parser("data", help="통계표 수치")
    data_args(d)
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_data)

    c = sub.add_parser("collect", help="수치를 파일로 저장")
    data_args(c)
    c.add_argument("--out-dir", default="output")
    c.add_argument("--name", default="kosis")
    c.add_argument("--format", default="xlsx,json")
    c.set_defaults(func=cmd_collect)
    return p


def main(argv: list[str] | None = None) -> int:
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
