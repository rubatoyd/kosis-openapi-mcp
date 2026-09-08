# kosis-openapi-mcp

<!-- mcp-name: io.github.rubatoyd/kosis-openapi-mcp -->

[![CI](https://github.com/rubatoyd/kosis-openapi-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/rubatoyd/kosis-openapi-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/rubatoyd/kosis-openapi-mcp)](https://github.com/rubatoyd/kosis-openapi-mcp/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/rubatoyd/kosis-openapi-mcp/total?label=downloads)](https://github.com/rubatoyd/kosis-openapi-mcp/releases)

<!-- usage:start -->
> 📈 **사용량** — 최근 14일 조회 **0**회(고유 0) · 클론 **0**회(고유 0) · 릴리스 자산 누적 다운로드 **0**
>
> ![일별 클론·조회 추이](docs/usage.svg)
>
> <sub>2026-09-07 자동 갱신 · 전체 이력은 [`docs/usage.csv`](docs/usage.csv). GitHub 트래픽 통계는 14일 창만 제공하므로 이 저장소가 매일 찍어 누적한다.</sub>
<!-- usage:end -->

**KOSIS(국가통계포털) 공유서비스 OpenAPI** 를 검색·수집하는 MCP 서버 + CLI.

통계표를 찾고, 항목·분류·주기를 확인하고, 수치를 받아 xlsx·csv·json·sqlite 로
내보낸다. **4만 셀 제한에 걸리면 기간을 알아서 쪼개** 전수를 회수한다.

자매 저장소: [law-openapi-mcp](https://github.com/rubatoyd/law-openapi-mcp)(법제처) ·
[na-openapi-mcp](https://github.com/rubatoyd/na-openapi-mcp)(국회도서관) ·
[nl-openapi-mcp](https://github.com/rubatoyd/nl-openapi-mcp)(국립중앙도서관) ·
[kci-openapi-mcp](https://github.com/rubatoyd/KCI_openAPI) · [scienceON-mcp](https://github.com/rubatoyd/scienceON-mcp)

---

## 무엇을 돌려주나

**통계 그 자체다.** 표의 메타(작성기관·조사명·수록기간·주기)와 수치(시점 × 분류 ×
항목 → 값)를 도메인 그대로 준다.

서지·인용 형식은 **부가 기능**(`kosis_citation`)으로 따로 두었다. 서지관리 도구로
넘길 때만 쓰면 되고, 그 도구가 아는 유형 목록이 통계 응답의 모양을 바꾸지는 않는다.

## 준비물

인증키 하나. [kosis.kr](https://kosis.kr) 회원가입 후 공유서비스 활용신청(자동 승인).

```bash
cp .env.example .env    # KOSIS_API_KEY=... 를 채운다
```

> 🔴 **발급된 값을 그대로 넣으세요.** base64 처럼 보여도(끝이 `=`) 디코드하면
> `err 11`(유효하지 않은 인증키)이 납니다.

## 설치·실행

```bash
uv sync
uv run kosis status
uv run kosis search 사교육비
uv run kosis meta --org 101 --tbl DT_1PE201 --kind ITM     # 항목 ID 확인
uv run kosis data --org 101 --tbl DT_1PE201 --prd Y --start 2020 --end 2025
uv run kosis collect --org 101 --tbl DT_1B040A3 --prd M --start 202101 --end 202512
# 분류축이 여럿인 표도 그대로 — 축 개수는 알아서 맞춘다(산업 × 규모)
uv run kosis data --org 118 --tbl DT_118N_MON051 --prd H --start 202401 --end 202401
# 주요지표(통계표와 다른 계열) — 표 구조를 몰라도 값까지 바로
uv run kosis indicator 출산율
uv run kosis indicator-data --id 13 --start 2020 --end 2025
```

MCP 등록:

```json
{
  "mcpServers": {
    "kosis": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rubatoyd/kosis-openapi-mcp", "kosis-mcp"],
      "env": { "KOSIS_API_KEY": "발급받은_값_그대로" }
    }
  }
}
```

> PyPI 에 올린 패키지가 아직 없어 **저장소에서 바로** 받아 쓴다(자매 저장소와 같은 방식).
> `main` 의 HEAD 를 쓰므로 다음 기동에 최신이 반영된다.

## MCP 도구

| 도구 | 하는 일 |
|------|---------|
| `kosis_status` | 인증키 보유 여부 + 실제 왕복 1회 |
| `kosis_guide` | 서비스뷰·주기·메타 종류·오류코드·한계·함정 |
| `kosis_search` | 통계표 찾기(통합검색) |
| `kosis_list` | 통계목록 트리 한 단계(주제별·기관별 …) |
| `kosis_meta` | 표의 항목(ITM)·분류(NCD)·주기(PRD)·출처 등 |
| `kosis_explain` | 통계설명(조사개요) |
| `kosis_data` | 수치 — **4만 셀 초과 시 기간 자동 분할** |
| `kosis_citation` | 표를 서지 칸으로 투영(부가 기능) |
| `kosis_collect` | 수치를 xlsx/csv/json/sqlite 로 저장 |
| `kosis_indicator_search` | **주요지표** 찾기(통계표와 다른 계열) — 페이징 전수 회수 |
| `kosis_indicator_data` | 주요지표의 시점별 수치 — **서버가 안 거르는 시점을 대신 거른다** |

## 알아 둘 것 (전부 실측)

172쪽짜리 공식 개발가이드가 있는데도 **가장 중요한 셋이 그 안에 없거나 틀리다**:

- 🔴 **`jsonVD=Y` 가 없으면 JSON 이 아니다** — 키에 따옴표가 없는 자바스크립트 객체
  리터럴이 온다. 이 파라미터는 가이드의 입력 변수 표에 **없고** JSP 예제 안에만 있다.
- 🔴 **통계자료를 `orgId`/`tblId` 로 부르려면 `/openapi/Param/statisticsParameterData.do`**
  를 써야 한다. 가이드가 표를 실어 둔 `statisticsData.do` 로 보내면 **항상 err 20**.
- 🔴 **인증키를 디코드하지 말 것.**

그 밖에:

- 🔴 **분류축(`objL`) 개수가 표의 축 수와 정확히 맞아야 한다** — 모자라면 `err 20 (objL)`,
  넘치면 `err 21`. 그런데 축 개수를 알려 주는 메타 서비스가 **없다**(`NCD` 는 분류가 아니라
  신규수록 시점이고 `OBJ`·`CLS` 는 `err 30`). `kosis_data` 가 축을 하나씩 늘려 맞추므로
  **다축 표(예: 산업 × 규모)도 그냥 부르면 된다** — 확정된 축은 `meta.obj_levels` 에 실린다.
- **모든 실패가 HTTP 200 이다.** 성공은 배열, 실패는 `{err, errMsg}` 객체.
  `Content-Type` 은 둘 다 `text/html` 이라 믿을 수 없다.
- **`err 30`(결과 없음)은 오류가 아니다** — 0건과 실패를 구분해서 보고한다.
- **요청당 4만 셀**(err 31) · **분당 200건**(err 40).
- **페이징이 없다** — 통합검색·목록·통계자료는 서버가 준 만큼이 전부다(조용히 자르지 않고 알린다).
  단 **통계주요지표 계열만 예외**로 `pageNo`·`numOfRows` 를 받고 안 주면 10건에서 잘린다 —
  `kosis_indicator_*` 가 끝까지 넘겨 전수를 회수한다([docs](docs/KOSIS_API_GUIDE.md) §7).
- 🔴 **주요지표 계열은 시점 범위를 거르지 않는다**(모드 스위치일 뿐 값은 무시된다).
  `kosis_indicator_data` 가 전 구간을 받아 직접 거르고 `meta.server_filtered=false` 로 알린다.
- `parentListId` 는 필수라고 적혀 있지만 **생략하면 최상위**가 온다.

자세한 근거와 재현 방법은 [docs/KOSIS_API_GUIDE.md](docs/KOSIS_API_GUIDE.md).

## 라이선스

MIT
