# 17 — 프론트엔드 아키텍처 (Svelte 5 종목 대시보드)

> 대상: `frontend/` (Vite + Svelte 5 + TypeScript + lightweight-charts v5)
> 상태: 구현 완료(mock 데이터). 실데이터/실시간 연동은 별도 — `HANDOFF-realtime-streaming.md` 참조.
> 선행 문서: [HANDOFF-frontend-svelte](HANDOFF-frontend-svelte.md)(이관 결정), [ui-design-brief](ui-design-brief.md)(디자인 브리프).

---

## 0. 한 줄 요약

토스증권 룩의 **종목 상세 대시보드**를 Svelte 5(runes)로 구현했다. 차트(lightweight-charts v5), 4탭(차트/종목정보/이벤트/리포트·컨센서스), 홈, 검색, AI 애널리스트 패널, 해시 라우터로 구성된 순수 static SPA다. 현재는 `public/mock/*.json` 기반이며, `api.ts`의 `BASE`만 `/mock` → `/api`로 바꾸면 실 백엔드로 전환된다(셰이프 일부는 조정 필요 — §7).

---

## 1. 기술 스택 / 결정

| 항목 | 선택 | 근거 |
|---|---|---|
| 프레임워크 | **Svelte 5 (runes)** | 보일러플레이트 최소, 경량, "단일 HTML" 감성. `$state`/`$derived`/`$effect`/`$props` 전용 |
| 빌드 | **Vite** (`svelte-ts` 템플릿) | 순수 static `dist/` 산출 |
| 차트 | **lightweight-charts v5** | TradingView 경량 차트. 캔들/볼륨/MA/VWAP |
| 라우팅 | **자체 해시 라우터** (외부 라이브러리 X) | `#/`, `#/stocks/{code}?tab=...&section=...` |
| 패키지 매니저 | **npm** (bun X) | — |
| 호스팅(예정) | FastAPI `StaticFiles` same-origin | alert_service pod에 dist 마운트(별도 작업) |

**가드레일**
- Svelte 4 문법 금지: `export let` / `$:` / `on:click` / `createEventDispatcher` / `$$props`. 전부 runes.
- 색/폰트/간격 **하드코딩 금지** — `design-tokens.css`의 `var()`만. 예외: `Chart.svelte`의 LWC 색 객체(`T`)와 `AiPanel`의 Gemini 로고 그라데이션(브랜드 마크).
- **한국 컨벤션**: 상승/매수 = 빨강 `#dc2e47`, 하락/매도 = 파랑 `#3182f6` (미국과 반대).

---

## 2. 디렉토리 구조

```
frontend/
  index.html                 # 진입 HTML (lang=ko, title=investview)
  package.json               # scripts: dev / build / preview / check
  public/
    favicon.svg, icons.svg
    mock/
      mock-data.json          # 종목 상세 1건(StockData) — 005930
      stocks.json             # 종목 리스트
      indices.json            # 지수 리스트(+sparkline)
  src/
    main.ts                   # 앱 마운트
    App.svelte                # 셸 + 라우팅 분기
    app.css                   # 전역 리셋
    design-tokens.css         # ★ 디자인 토큰(색/폰트/간격/radius/모션)
    lib/
      stores.svelte.ts        # 라우팅 상태($state) + parseHash/navigate/initRouter
      types.ts                # 데이터 타입(StockData 등)
      api.ts                  # 데이터 fetch (현재 /mock)
      format.ts               # fmtPrice/fmtVol/pct/changeClass
      Gnb.svelte              # 글로벌 내비 + 로고 + 검색 진입
      SearchOverlay.svelte    # 종목 검색 오버레이(/ 단축키)
      Home.svelte             # 홈(지수 카드 + 종목 리스트)
      StockDetail.svelte      # 종목 상세 컨테이너(데이터 fetch + 탭 분기)
      StockHeader.svelte      # 헤더(종목명/가격 + 우측 미니지표)
      TabBar.svelte           # 탭 4개 전환
      ChartView.svelte        # [차트탭] Chart + MetricsGrid + AiPanel 레이아웃
        Chart.svelte          #   캔들/볼륨/MA/VWAP/이벤트레인/측정도구
        MetricsGrid.svelte    #   체결강도 도넛 / 투자지표 스파크라인 / 컨센서스 / AI재무
        AiPanel.svelte        #   AI 애널리스트(접기/펼치기, Gemini 로고)
      StockInfo.svelte        # [종목정보탭] 기업개요/투자지표/재무/수급(섹션 앵커)
      EventsTab.svelte        # [이벤트탭] 타임라인
      ReportsTab.svelte       # [리포트·컨센서스탭] 목표주가/투자의견/리포트
```

---

## 3. 컴포넌트 트리 / 라우팅

```
main.ts → App.svelte
  ├─ Gnb (onNavigate)                      ← 로고 클릭=홈, 검색(/), 메뉴
  │    └─ SearchOverlay (searchOpen)
  └─ content-shell
       ├─ route.view==='home'  → Home (지수 카드 + 종목 행 클릭→navigate)
       └─ route.code!=null     → StockDetail(code, tab)
            ├─ StockHeader (meta, snapshot, tickDetail)   ← 가격 + 우측 4지표
            ├─ TabBar (activeTab, onSelect)
            └─ tab 분기:
                 chart   → ChartView(data)
                            ├─ Chart(candles, timeline, snapshot)
                            ├─ MetricsGrid(tickDetail, indicators, consensus, fundamentals, snapshot)
                            └─ AiPanel(data)
                 info    → StockInfo(data, section)   ← section 앵커로 스크롤
                 events  → EventsTab(data)
                 reports → ReportsTab(data)
```

### 라우팅 (`stores.svelte.ts`)
- `appState.route` = `$state<{view, code?, tab?, section?}>` — 전역 반응 상태.
- `parseHash()`: `#/` → home, `#/stocks/{code}?tab=chart&section=flow` → stock. `tab` 기본 `chart`.
- `navigate(path)`: `window.location.hash = path`. `hashchange` 리스너가 `appState.route` 갱신.
- `App.svelte`의 `$effect`에서 `initRouter()` 1회 호출.
- **섹션 네비게이트**: 메트릭 카드 클릭 → `navigate('/stocks/{code}?tab=info&section=flow|indicators')` → `StockInfo`가 마운트 시 `#section-*`로 `scrollIntoView` + 하이라이트. 컨센서스 카드는 `?tab=reports`.

---

## 4. 상태관리 / 데이터 흐름

```
StockDetail: data = $state<StockData|null>(null)
   └ $effect → getStockData(code) → data = d        (현재 1회 fetch)
        ↓ props
   StockHeader/MetricsGrid/AiPanel/StockInfo... = $props() + $derived(...)
```

- **단방향 props + 파생값**: 자식은 `$props()`로 받아 `$derived`로 표시값 계산. `data`가 새 객체로 바뀌면 **자동 재렌더**(Svelte 5 fine-grained reactivity).
- `stores.svelte.ts`는 **라우팅 전용**(라이브 데이터 스토어 아님).
- 데이터 소스: `api.ts`
  - `getStockData(symbol)` → `/mock/mock-data.json` (전체 `StockData` 통합 1건)
  - `getStockList()` → `/mock/stocks.json`, `getIndices()` → `/mock/indices.json`
  - `const BASE = '/mock'` — 실 백엔드 전환 시 `/api`로. (단 §7 셰이프 주의)

---

## 5. 디자인 토큰 (`design-tokens.css`)

모든 패널은 여기 정의된 `var()`만 사용한다(하드코딩 차단 → AI티 "visual averaging" 방지).

```
색(한국 컨벤션):
  --color-positive #dc2e47 (상승/매수=RED)   --color-negative #3182f6 (하락/매도=BLUE)
  --color-flat #8a8f98       --brand #3182f6 (CTA, =negative지만 맥락 구분)
표면(그림자 대신 레이어 alpha):
  --surface-floor #101013  --surface-body #17171c  --surface-overlay #202025  --surface-raised #26262c
보더(반투명):  --border-subtle / --border-strong
텍스트:        --text-primary / --text-secondary / --text-tertiary   --text-on-brand #fff
폰트:          --font-sans (Pretendard)   --font-mono (Geist Mono, 숫자)
간격(8pt):     --space-1/2/3/4/6/8  (※ --space-5 없음)
radius:        --radius-sm/md/lg
모션:          --ease-out  --lift-card -2px  --dur-hover .18s  --dur-fill .62s
overlay:       --overlay-scrim  --shadow-overlay
유틸:          .tnum(tabular-nums)  .price-up/.price-down/.price-flat  .panel
```

---

## 6. 화면별 핵심

- **홈**: 지수 카드(인라인 SVG 스파크라인) + 종목 리스트. 행 클릭 → 상세.
- **차트 탭**(`ChartView` = 좌 차트영역 + 우 AI패널):
  - `Chart.svelte`: LWC v5 캔들 + 볼륨 + MA5/20/60 + VWAP 라인, 이벤트 레인(알림/패턴 마커), 크로스헤어 OHLC 레전드, Shift+드래그 구간 측정, VI 발동가 price line, 타임프레임 버튼.
  - `MetricsGrid.svelte`: ① 체결강도 **SVG 도넛 게이지**(매수 빨강/매도 파랑 호, 마운트 채움 애니메이션) ② 투자지표 **PER/EPS/ROE 스파크라인**(분기 추세 + 전분기 델타) ③ 컨센서스 **목표주가 바**(현재가↔목표가 + 리포트 행) ④ AI 재무요약(접이식). 카드 hover ghost-lift, 클릭 → 종목정보/리포트 네비게이트.
  - `AiPanel.svelte`: AI 애널리스트 채팅(mock 응답). **`»` 토글로 접기/펼치기**(접힘=얇은 레일, localStorage 영속), Gemini 로고, 추천 칩 + 입력창.
- **종목정보 탭**(`StockInfo`): 기업개요 / 투자지표 / 재무(분기 매출 바 + 요약) / 수급(외국인·기관·개인 순매수). 각 `<section id="section-*">` — 차트 카드에서 앵커 스크롤 진입.
- **이벤트 탭**(`EventsTab`): 알림/패턴/공시/실적/배당 통합 타임라인.
- **리포트·컨센서스 탭**(`ReportsTab`): 목표주가 컨센서스 추이 + 투자의견 분포 + 증권사 리포트.
- **헤더**(`StockHeader`): 종목명/코드 + 가격/등락(색), 우측에 체결강도/거래량/VI발동가/VWAP 한눈 지표.
- **GNB/검색**: 로고(investview), 메뉴, 검색 pill(`/` 단축키) → `SearchOverlay`(Esc 닫기).

---

## 7. 실데이터 전환 메모 (요약 — 상세는 HANDOFF-realtime-streaming)

- 백엔드 REST `/api/candles|snapshot|timeline/{symbol}`는 프론트 타입과 **셰이프 정확 일치**. 단 현재 `getStockData`는 **단일 통합 JSON**을 받으므로, 실 API 전환 시 **리소스별 분리 fetch**로 분해 필요.
- `tickDetail`/`indicators`/`consensus`/`fundamentals`는 백엔드 소스 없음 → 당분간 mock 유지.
- `_meta.stock_name`은 어떤 API에도 없음 → snapshot 확장 or `/api/stock-info` 필요.
- 실시간: `Chart.svelte`가 `$effect` 안에서 `setData`로 전체 재생성 → 실시간엔 `series.update()` 증분 패턴으로 리팩터 필요(줌/스크롤 보존).

---

## 8. 개발 / 빌드 / 검증

```bash
cd frontend
npm install
npm run dev         # 개발 서버(HMR)
npm run build       # 순수 static dist/ 산출
npm run preview     # 빌드 결과 미리보기
npm run check       # svelte-check + tsc (0 errors 기준)
```

**품질 게이트**(이 프로젝트 컨벤션)
- `npm run build` exit 0 + `npm run check` 0 errors/0 warnings
- Svelte 4 누수 grep 0: `grep -rnE "export let |on:[a-z]+=|createEventDispatcher|\$\$props" src --include='*.svelte'`
- 하드코딩 색 grep: `Chart.svelte`의 `T` 색 객체 + `AiPanel` Gemini 그라데이션만 허용
- 1280/1440 폭 가로 스크롤바 없음, 콘솔 에러 0 (Playwright)
