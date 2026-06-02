<script lang="ts">
  import type { TickDetail, Indicators, Consensus, Fundamentals, Snapshot } from './types';
  import { fmtPrice, pct, changeClass } from './format';
  import { navigate, appState } from './stores.svelte';

  let {
    tickDetail,
    indicators,
    consensus,
    fundamentals,
    snapshot
  }: {
    tickDetail: TickDetail;
    indicators: Indicators;
    consensus: Consensus[];
    fundamentals: Fundamentals;
    snapshot: Snapshot;
  } = $props();

  let code = $derived(snapshot?.symbol ?? appState.route.code ?? '');
  function goInfo(section: string) {
    navigate('/stocks/' + code + '?tab=info&section=' + section);
  }
  function goReports() {
    navigate('/stocks/' + code + '?tab=reports');
  }

  let strength = $derived(tickDetail?.trade_strength ?? 0);
  let strengthClass = $derived(strength >= 100 ? 'price-up' : 'price-down');
  let buyPct = $derived((tickDetail?.buy_ratio ?? 0.5) * 100);
  let netBuy = $derived(tickDetail?.net_buy_count ?? 0);
  let netBuyClass = $derived(netBuy >= 0 ? 'price-up' : 'price-down');

  let barReady = $state(false);
  $effect(() => {
    const id = requestAnimationFrame(() => barReady = true);
    return () => cancelAnimationFrame(id);
  });

  // ---- 체결강도 도넛 게이지 (SVG) -----------------------------------------
  // 반지름 42 → 둘레 C = 2π·42. 매수 호(빨강)/매도 호(파랑)를 dashoffset로 채움.
  const GAUGE_C = 2 * Math.PI * 42;
  let buyFrac = $derived(Math.max(0, Math.min(1, buyPct / 100)));
  let sellFrac = $derived(1 - buyFrac);
  // 비어있는 상태(offset=C) → mount 후 목표치로 트랜지션 (barReady 패턴 재사용)
  let buyOffset = $derived(barReady ? GAUGE_C * (1 - buyFrac) : GAUGE_C);
  let sellOffset = $derived(barReady ? GAUGE_C * (1 - sellFrac) : GAUGE_C);
  // 매도 호는 매수 호가 끝나는 각도(-90° 기준 + 매수비율만큼 회전)에서 시작
  let sellRot = $derived(-90 + buyFrac * 360);

  // ---- 투자지표 분기 추세 (DUMMY 시계열: 마지막 값 = 실제 indicators 값) ----
  // 분기별 시계열은 mock-data.json에 없어 컴포넌트-로컬 더미로 구성 (StockInfo 컨벤션).
  const trendQuarters = ['25Q2', '25Q3', '25Q4', '26Q1'];
  const epsTrend = [4210, 4480, 4760, 5070];   // DUMMY 분기별 EPS, 마지막=indicators.eps(5070)
  const roeTrend = [8.1, 8.9, 9.4, 9.84];        // DUMMY 분기별 ROE, 마지막=indicators.roe(9.84)
  const perTrend = [16.8, 15.9, 14.9, 14.2];     // DUMMY 분기별 PER, 마지막=indicators.per(14.2)

  // 0~100/0~28 viewBox 좌표로 정규화한 스파크라인 polyline points (Home.svelte 패턴)
  function sparkPoints(vals: number[]): string {
    if (!vals || vals.length < 2) return '';
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const step = 100 / (vals.length - 1);
    return vals
      .map((v, i) => `${(i * step).toFixed(1)},${(28 - ((v - min) / span) * 24 - 2).toFixed(1)}`)
      .join(' ');
  }
  // 직전 분기 대비 증감률 (한국 관례: 증가=빨강, 감소=파랑)
  function trendDelta(vals: number[]): number {
    const last = vals[vals.length - 1];
    const prev = vals[vals.length - 2];
    return prev ? ((last - prev) / prev) * 100 : 0;
  }

  let trendList = $derived([
    { label: 'PER (이익 대비)', value: indicators?.per, suffix: '배', whole: false, series: perTrend },
    { label: 'EPS (주당순이익)', value: indicators?.eps, suffix: '원', whole: true, series: epsTrend },
    { label: 'ROE (자본 효율)', value: indicators?.roe, suffix: '%', whole: false, series: roeTrend }
  ]);
  let restList = $derived([
    { label: 'PBR (자산 대비)', value: indicators?.pbr, suffix: '배', whole: false },
    { label: 'BPS (주당순자산)', value: indicators?.bps, suffix: '원', whole: true },
    { label: '배당수익률', value: indicators?.dividend_yield, suffix: '%', whole: false }
  ]);

  let hasConsensus = $derived(consensus && consensus.length > 0);
  let targets = $derived(hasConsensus ? consensus.map(c => c.target_price) : []);
  let avgTarget = $derived(hasConsensus ? Math.round(targets.reduce((a, b) => a + b, 0) / targets.length) : 0);
  let currentPrice = $derived(snapshot?.last_price || avgTarget);
  let upside = $derived(currentPrice ? ((avgTarget - currentPrice) / currentPrice) * 100 : 0);
  let upsideClass = $derived(upside >= 0 ? 'price-up' : 'price-down');
  
  let lo = $derived(hasConsensus ? Math.min(currentPrice, ...targets) * 0.98 : 0);
  let hi = $derived(hasConsensus ? Math.max(currentPrice, ...targets) * 1.02 : 1);
  let pos = $derived((v: number) => ((v - lo) / (hi - lo)) * 100);
  
  let buyCount = $derived(hasConsensus ? consensus.filter(c => c.investment_opinion === 'Buy').length : 0);
  let holdCount = $derived(hasConsensus ? consensus.filter(c => c.investment_opinion === 'Hold').length : 0);
  let isBuy = $derived(buyCount >= holdCount);

  let aiCollapsed = $state(true);
  let chips = $derived.by(() => {
    const c = [];
    if (fundamentals?.market) c.push(fundamentals.market);
    if (fundamentals?.industry_name) c.push(fundamentals.industry_name);
    if (fundamentals?.market_cap) c.push('시총 ' + Math.round(fundamentals.market_cap / 1e12) + '조');
    return c;
  });
  let aiSections = $derived.by(() => {
    return [
      { t: '재무상태', x: fundamentals?.balance_sheet_summary },
      { t: '손익', x: fundamentals?.income_statement_summary },
      { t: '현금흐름', x: fundamentals?.cash_flow_summary }
    ].filter(s => s.x);
  });
</script>

<div class="metrics-grid">
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="metric-card">
    <div class="mc-head nav-head" onclick={() => goInfo('flow')}>
      <span class="mc-title">체결강도</span>
      <span class="nav-hint">종목정보<span class="mc-arrow">›</span></span>
    </div>
    <div class="gauge-wrap">
      <div class="gauge">
        <svg class="gauge-svg" viewBox="0 0 100 100" aria-hidden="true">
          <circle class="g-track" cx="50" cy="50" r="42" />
          <circle
            class="g-arc g-buy"
            cx="50" cy="50" r="42"
            style="stroke-dasharray: {GAUGE_C}; stroke-dashoffset: {buyOffset};"
          />
          <circle
            class="g-arc g-sell"
            cx="50" cy="50" r="42"
            style="stroke-dasharray: {GAUGE_C}; stroke-dashoffset: {sellOffset}; transform: rotate({sellRot}deg);"
          />
        </svg>
        <div class="gauge-center">
          <span class="g-value tnum {strengthClass}">
            {tickDetail?.trade_strength != null ? tickDetail.trade_strength.toFixed(1) + '%' : '—'}
          </span>
          <span class="g-cap">체결강도</span>
        </div>
      </div>
    </div>
    <div class="mc-submetrics">
      <div class="sm">
        <span class="sm-label">매수</span>
        <span class="sm-val tnum price-up">{tickDetail?.buy_count != null ? tickDetail.buy_count.toLocaleString('ko-KR') : '—'}</span>
      </div>
      <div class="sm">
        <span class="sm-label">매도</span>
        <span class="sm-val tnum price-down">{tickDetail?.sell_count != null ? tickDetail.sell_count.toLocaleString('ko-KR') : '—'}</span>
      </div>
      <div class="sm">
        <span class="sm-label">순매수</span>
        <span class="sm-val tnum {netBuyClass}">
          {tickDetail?.net_buy_count != null ? (netBuy >= 0 ? '+' : '') + netBuy.toLocaleString('ko-KR') : '—'}
        </span>
      </div>
    </div>
  </div>

  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="metric-card">
    <div class="mc-head nav-head" onclick={() => goInfo('indicators')}>
      <span class="mc-title">투자지표</span>
      <span class="nav-hint">종목정보<span class="mc-arrow">›</span></span>
    </div>
    <div class="trend-list">
      {#each trendList as t}
        {@const d = trendDelta(t.series)}
        <div class="trend-row">
          <div class="tr-info">
            <span class="tr-label">{t.label}</span>
            <span class="tr-value tnum">
              {#if t.value == null}
                —
              {:else}
                {t.whole ? Math.round(t.value).toLocaleString('ko-KR') : t.value.toFixed(2)}<small>{t.suffix}</small>
              {/if}
            </span>
            <span class="tr-delta tnum {changeClass(d)}">
              {d >= 0 ? '▲' : '▼'} {pct(Math.abs(d), false)}
            </span>
          </div>
          <svg class="tr-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
            <polyline
              points={sparkPoints(t.series)}
              fill="none"
              stroke="var({d >= 0 ? '--color-positive' : '--color-negative'})"
              stroke-width="1.5"
              vector-effect="non-scaling-stroke"
              stroke-linejoin="round"
              stroke-linecap="round"
            />
          </svg>
        </div>
      {/each}
    </div>
    <div class="rest-grid">
      {#each restList as ind}
        <div class="ind-cell">
          <span class="ind-label">{ind.label}</span>
          <span class="ind-value tnum">
            {#if ind.value == null}
              —
            {:else}
              {ind.whole ? Math.round(ind.value).toLocaleString('ko-KR') : ind.value.toFixed(2)}<small>{ind.suffix}</small>
            {/if}
          </span>
        </div>
      {/each}
    </div>
  </div>

  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="metric-card mc-consensus">
    <div class="mc-head nav-head" onclick={goReports}>
      <span class="mc-title">컨센서스</span>
      <span class="mc-head-right">
        <span class="opinion-badge {isBuy ? 'buy' : 'hold'}">
          {isBuy ? '매수' : '중립'} (Buy {buyCount}·Hold {holdCount})
        </span>
        <span class="nav-hint">리포트<span class="mc-arrow">›</span></span>
      </span>
    </div>
    {#if hasConsensus}
      <div class="consensus-body">
        <div class="consensus-target">
          <div class="ct-row">
            <span class="ct-label">목표주가 평균</span>
            <span class="ct-value tnum">{fmtPrice(avgTarget)}</span>
            <span class="ct-upside tnum {upsideClass}">
              {(upside >= 0 ? '+' : '') + upside.toFixed(1)}%
            </span>
          </div>
          <div class="ct-bar">
            <div class="ct-track"></div>
            <div class="ct-fill" style="left: {Math.min(pos(currentPrice), pos(avgTarget))}%; width: {Math.abs(pos(avgTarget) - pos(currentPrice))}%"></div>
            <div class="ct-current" style="left: {pos(currentPrice)}%"></div>
            <div class="ct-goal" style="left: {pos(avgTarget)}%"></div>
          </div>
          <div class="ct-scale">
            <span class="ct-scale-cur tnum">현재 {fmtPrice(currentPrice)}</span>
            <span class="ct-scale-goal tnum">목표 {fmtPrice(avgTarget)}</span>
          </div>
        </div>
        <div class="report-list">
          {#each consensus.slice(0, 3) as c}
            <div class="report-row">
              <span class="rr-provider">{c.provider}</span>
              <span class="rr-target tnum">{fmtPrice(c.target_price)}</span>
              <span class="rr-opinion {c.investment_opinion === 'Buy' ? 'buy' : ''}">
                {c.investment_opinion === 'Buy' ? '매수' : (c.investment_opinion === 'Hold' ? '중립' : c.investment_opinion)}
              </span>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="metric-card mc-ai">
    <div class="mc-head ai-head" class:open={!aiCollapsed} onclick={() => aiCollapsed = !aiCollapsed}>
      <span class="mc-title">✨ AI 재무 요약</span>
      <span class="ai-chevron">▾</span>
    </div>
    <div class="ai-chips">
      {#each chips as chip}
        <span class="ai-chip">{chip}</span>
      {/each}
    </div>
    <div class="ai-body" class:collapsed={aiCollapsed}>
      {#each aiSections as s}
        <div class="ai-section">
          <div class="ai-section-title">{s.t}</div>
          <div class="ai-section-text">{s.x}</div>
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  .metrics-grid {
    width: 100%;
    box-sizing: border-box;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-3);
    margin-top: var(--space-3);
  }

  .metric-card {
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    transition: transform var(--dur-hover) var(--ease-out),
                border-color var(--dur-hover) var(--ease-out),
                background var(--dur-hover) var(--ease-out);
  }

  .metric-card:hover {
    transform: translateY(var(--lift-card));
    border-color: var(--border-strong);
  }
  .metric-card:not(.mc-ai):hover {
    background: var(--surface-overlay);
  }

  .mc-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-4);
  }

  .mc-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  .nav-head { cursor: pointer; }
  .mc-head-right { display: flex; align-items: center; gap: var(--space-2); }
  .nav-hint {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    color: var(--text-tertiary);
    transition: color var(--dur-hover) var(--ease-out);
  }
  .nav-head:hover .nav-hint { color: var(--brand); }
  .mc-arrow {
    color: var(--text-tertiary);
    font-size: 14px;
    font-weight: 700;
    transition: transform var(--dur-hover) var(--ease-out),
                color var(--dur-hover) var(--ease-out);
  }
  .nav-head:hover .mc-arrow { transform: translateX(2px); color: var(--brand); }

  .gauge-wrap {
    display: flex;
    justify-content: center;
    margin-bottom: var(--space-4);
  }
  .gauge {
    position: relative;
    width: 140px;
    height: 140px;
  }
  .gauge-svg { width: 100%; height: 100%; }
  .g-track {
    fill: none;
    stroke: var(--surface-raised);
    stroke-width: 10;
  }
  .g-arc {
    fill: none;
    stroke-width: 10;
    stroke-linecap: round;
    transform-box: fill-box;
    transform-origin: center;
    transition: stroke-dashoffset var(--dur-fill) var(--ease-out);
  }
  .g-buy {
    stroke: var(--color-positive);
    transform: rotate(-90deg);
  }
  .g-sell { stroke: var(--color-negative); }
  .gauge-center {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    pointer-events: none;
  }
  .g-value {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .g-cap { font-size: 11px; color: var(--text-tertiary); }

  .mc-submetrics {
    display: flex;
    justify-content: space-between;
    gap: var(--space-2);
    margin-top: auto;
  }

  .sm {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .sm-label { font-size: 11px; color: var(--text-tertiary); }
  .sm-val { font-size: 13px; font-weight: 500; color: var(--text-primary); }

  .trend-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
  }
  .trend-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
  }
  .tr-info {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    flex-wrap: wrap;
    min-width: 0;
  }
  .tr-label { font-size: 11px; color: var(--text-tertiary); flex-shrink: 0; }
  .tr-value { font-size: 17px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.02em; }
  .tr-value small { font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-left: 1px; }
  .tr-delta { font-size: 11px; font-weight: 600; }
  .tr-spark {
    width: 64px;
    height: 28px;
    flex-shrink: 0;
  }

  .rest-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-3);
    margin-top: auto;
    padding-top: var(--space-3);
    border-top: 1px solid var(--border-subtle);
  }

  .ind-cell { display: flex; flex-direction: column; gap: 4px; }
  .ind-label { font-size: 11px; color: var(--text-tertiary); }
  .ind-value { font-size: 17px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.02em; }
  .ind-value small { font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-left: 1px; }

  .opinion-badge {
    font-size: 12px;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 999px;
  }
  .opinion-badge.buy { color: var(--color-positive); background: color-mix(in srgb, var(--color-positive) 12%, transparent); }
  .opinion-badge.hold { color: var(--text-secondary); background: var(--surface-raised); }

  .mc-consensus { grid-column: 1 / -1; }
  .consensus-body {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    gap: var(--space-6);
    align-items: start;
  }

  .ct-row { display: flex; align-items: baseline; gap: var(--space-2); margin-bottom: var(--space-3); }
  .ct-label { font-size: 11px; color: var(--text-tertiary); }
  .ct-value { font-size: 16px; font-weight: 700; color: var(--text-primary); }
  .ct-upside { font-size: 12px; font-weight: 600; }

  .ct-bar { position: relative; height: 18px; }
  .ct-track {
    position: absolute;
    top: 50%;
    left: 0; right: 0;
    height: 2px;
    transform: translateY(-50%);
    background: var(--surface-raised);
    border-radius: 1px;
  }
  .ct-fill {
    position: absolute;
    top: 50%;
    height: 2px;
    transform: translateY(-50%);
    background: color-mix(in srgb, var(--color-positive) 55%, transparent);
    border-radius: 1px;
    transition: width var(--dur-fill) var(--ease-out), left var(--dur-fill) var(--ease-out);
  }
  .ct-current {
    position: absolute;
    top: 50%;
    width: 9px; height: 9px;
    border-radius: 50%;
    background: var(--text-primary);
    transform: translate(-50%, -50%);
    transition: box-shadow var(--dur-hover) var(--ease-out);
  }
  .mc-consensus:hover .ct-current {
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--text-primary) 14%, transparent);
  }
  .ct-goal {
    position: absolute;
    top: 0; bottom: 0;
    width: 2px;
    background: var(--color-positive);
    transform: translateX(-50%);
  }
  .ct-scale {
    display: flex;
    justify-content: space-between;
    margin-top: var(--space-2);
    font-size: 10px;
    color: var(--text-tertiary);
  }
  .ct-scale-goal { color: var(--color-positive); }

  .report-list { display: flex; flex-direction: column; gap: var(--space-1); }
  .report-row {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    font-size: 12px;
    padding: var(--space-1) var(--space-2);
    margin: 0 calc(-1 * var(--space-2));
    border-radius: var(--radius-sm);
    transition: background var(--dur-hover) var(--ease-out);
  }
  .report-row:hover { background: var(--surface-raised); }
  .rr-provider { color: var(--text-secondary); flex: 1; }
  .rr-target { color: var(--text-primary); font-weight: 600; }
  .rr-opinion { font-size: 11px; color: var(--text-tertiary); }
  .rr-opinion.buy { color: var(--color-positive); }

  .mc-ai { grid-column: 1 / -1; }
  .ai-head { cursor: pointer; margin-bottom: var(--space-3); }
  .ai-chevron { color: var(--text-tertiary); font-size: 12px; transition: transform 0.2s; }
  .ai-head.open .ai-chevron { transform: rotate(180deg); }

  .ai-chips { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
  .ai-chip {
    font-size: 11px;
    color: var(--text-secondary);
    background: var(--surface-overlay);
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    padding: 3px 10px;
  }

  .ai-body {
    background: var(--surface-overlay);
    border-radius: var(--radius-sm);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .ai-body.collapsed { max-height: 76px; overflow: hidden; }
  .ai-section-title { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 4px; }
  .ai-section-text { font-size: 12px; line-height: 1.6; color: var(--text-secondary); }
</style>