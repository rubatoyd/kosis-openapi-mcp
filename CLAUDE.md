# kosis-openapi-mcp — 프로젝트 지침

> **KOSIS 국가통계포털 공유서비스**(kosis.kr/openapi) 수집기. 공개 **MCP 서버 + CLI**.
> 자매 저장소 **law-openapi-mcp**(`../LAW openAPI`) · **na-openapi-mcp**(`../NA openAPI`) ·
> **nl-openapi-mcp** · **kci-openapi-mcp** · **scienceON-mcp** 와 동일 아키텍처.
> 규격 → [docs/KOSIS_API_GUIDE.md](docs/KOSIS_API_GUIDE.md) · 원문 → `ref/KOSIS 공유서비스 개발가이드.pdf`(172쪽)

## 1. 🔴 이 저장소가 돌려주는 것은 **통계 그 자체**다

1차 산출물은 **표의 메타**(작성기관·조사명·수록기간·주기)와 **수치**(시점 × 분류 ×
항목 → 값)다. 도메인 그대로 준다.

서지·인용 형식은 **소비하는 쪽의 관심사**이지 이 API 의 모양이 아니다.
`kosis_citation` 은 **선택적 투영** 하나일 뿐이고, 서지 도구가 아는 유형 목록이
통계 응답의 스키마를 바꾸지 않는다. ⛔ 특정 서지 도구 전용 어댑터로 만들지 말 것 —
그 도구가 바뀔 때마다 이 저장소가 흔들린다.

KOSIS 는 **조사(STAT_NM)와 표(TBL_NM) 두 층을 다 주므로** 레코드에 둘 다 싣는다.
인용하는 쪽이 어느 층으로 적을지 고르면 된다. 수치 한 칸(관측치)은 문헌이 아니라 위치다.

## 2. 확정 결정사항
| 항목 | 결정 |
|------|------|
| 언어/런타임 | Python 3.10+ |
| 패키지 관리 | **uv**. venv 는 **클라우드 폴더 밖** `C:/Users/rubat/.venvs/kosis-openapi-mcp` |
| 의존성 | mcp(FastMCP, **`<2` 상한 필수**), requests, openpyxl, python-dotenv, truststore |
| 인터페이스 | 공용 코어 + **MCP 서버(server.py)** + **CLI(cli.py)** |
| 출력 | xlsx · csv · json · sqlite |
| 공개 | MIT. `.env`·`.claude/settings.local.json`·`output/`·`probe-out/` 는 gitignore |

## 3. 구조
```
src/kosis_mcp/
  config.py     # 엔드포인트, 서비스뷰·메타종류·주기·오류코드, 한계, scrub
  models.py     # Table(통계표) · Observation(수치) + 정제·정규화
  parser.py     # JSON 파싱 · 오류 봉투 판별 · jsonVD 누락 진단
  client.py     # 호출·재시도·🔴 4만 셀 자동 분할
  exporters.py  # xlsx/csv/json/sqlite (분류 축이 가변이라 열도 가변)
  server.py     # MCP 도구 9종
  cli.py        # status/guide/search/list/meta/explain/data/collect
scripts/probe_api.py   # ★ 라이브 탐침 — 인증·모양·상한
tests/fixtures/        # ★ 2026-09-08 의 진짜 응답
```

## 4. 🔴 핵심 기술사실 (2026-09-08 라이브 실측)

172쪽짜리 공식 가이드가 있는데도 **가장 중요한 셋이 그 안에 없거나 틀리다.**

### (A) `jsonVD=Y` 가 없으면 JSON 이 아니다
`format=json` 만 주면 **키에 따옴표가 없는 자바스크립트 객체 리터럴**이 온다.
이 파라미터는 개발가이드의 **입력 변수 표에 없고** JSP 예제 소스 안에만 있다.
→ `client._get` 이 항상 붙인다. 파서는 빠졌을 때 **원인을 정확히 짚는다**.

### (B) 통계자료 파라미터 방식은 **경로가 다르다**
`statisticsData.do` 로 `orgId`/`tblId` 를 보내면 **항상 err 20**(조합 11가지 시도).
실제 경로는 **`/openapi/Param/statisticsParameterData.do`** 이고 문서 어디에도 없다.
→ `ENDPOINTS["param"]`. 회귀 테스트가 이 경로를 붙들고 있다.

### (C) 인증키를 디코드하지 말 것
base64 로 보이지만 **발급값 그대로** 보내야 한다. 풀어서 보내면 err 11.

### (D) 모든 실패가 HTTP 200 · 성공은 배열, 실패는 객체
`Content-Type` 은 둘 다 `text/html` 이라 믿을 수 없다. 대용량 서비스만 XML 오류.
⚠️ **`method` 를 빠뜨리면 빈 본문**이 온다 — '0건'으로 읽으면 조용한 사고다.

### (E) 🔴 err 31 은 실패가 아니라 **분할 신호**다
요청당 4만 셀 제한. 실측: 12개월 10,590행 ✅ / 60개월 ❌ err 31.
→ 기간을 주면 **절반씩 쪼개 자동 재요청**한다. 60개월 구간을 3회 요청으로
   **52,587행 전수** 회수(구간 겹침 없음, 회귀로 고정).
⚠️ `recent`(최근 N개)는 쪼갤 축이 없어 자동 분할이 안 된다 — 그 사실을 알린다.

### (F) 문서와 다른 것들
- `parentListId` 는 **필수가 아니다** — 생략하면 최상위 목록이 온다(트리의 입구).
- `method` 값은 검증되지 않는다(`method=nope` 도 정상 결과).
- 페이징이 **없다** — 서버가 준 만큼이 전부. 조용히 자르지 말고 알린다.
- 통계설명 본문에 **두 번 이스케이프된** 엔티티(`&amp;ldquo;`).
- 분류 축 개수가 **표마다 다르다**(C1~C8) — 고정 스키마를 강요하면 분류가 사라진다.

## 5. 개발 원칙 (자매 저장소 공통 — 이미 값을 치른 것들)
- 자격증명은 `.env`/MCP env 로만. **`raise_for_status()` 금지** — URL 을 예외에 박는다.
  예외 문구에 **요청 URL 을 싣지 않는다**(인증키가 화면·스크린샷으로 나간다).
- **조용한 절단 금지** — 총건수·절단 사유를 메타로 노출. 빈 응답을 '0건'으로 통과 금지.
- **0건과 실패를 구분한다** — err 30 은 `no_data:true` 로, 실패는 예외로.
  소비하는 쪽이 ⚠ 로 띄울 수 있어야 한다.
- 미매핑 필드를 버리지 않는다(내보내기에 열로 승격).
- 설정 오류(인증키 누락)는 **재시도 루프에 들여보내지 않는다**.
- 로그 scrubber 는 **문자열 인자만** 건드린다 — 전부 `str()` 로 바꾸면 `%d` 포맷이
  터진다(자매 저장소 law-openapi-mcp 에서 실제로 겪고 회귀로 고정).
- 전송 기본값은 **stdio 로 못박는다**.
- 정중한 호출: 분당 200건 제한 → throttle 0.35초(≈171/분). err 40 은 기다렸다 재시도.
- **라이브 검증 우선(추정 금지)** — 문서가 172쪽이어도 실측이 이긴다.
  ⚠️ **자매 저장소의 사실을 이식하지 말 것.**
- **커밋 메시지 한국어, Claude 서명 금지.**

## 6. 상태 (2026-09-08)
- ✅ **v0.1.0 코어 완성** — config·models·parser·client·exporters·server·cli +
  회귀 **41건**. 라이브 검증: status 왕복 · 통합검색(사교육비 20건) · 목록 트리
  최상위 30건 · 메타 ITM 34건 · 통계자료 868행 · **err 31 자동 분할로 52,587행**.
- 🔬 **문서에 없는 것 셋을 실측으로 찾았다**(§4 A·B·C). 특히 (B)는 문서만 보고는
  절대 알 수 없다 — 가이드의 예제 URL 이 전부 `userStatsId` 방식뿐이다.
- ⏭️ 다음: ① 대용량(BigData) 서비스 — 같은 키로 err 11, 별도 활용신청 여부 확인(❓)
  ② 통계주요지표 계열(indiListService 등) 검토 ③ CI 이식 ④ 릴리스(지시 대기)
