<script lang="ts">
  import type { StockData } from './types';
  import { fmtPrice } from './format';

  let { data }: { data: StockData } = $props();

  // 컨센서스 리스트 (증권사 리포트). 최신순 정렬.
  let reports = $derived(
    [...(data?.consensus ?? [])].sort((a, b) => b.report_date.localeCompare(a.report_date))
  );

  // 목표주가 평균 (현재 컨센서스 스냅샷)
  let avgTarget = $derived(
    reports.length ? Math.round(reports.reduce((s, r) => s + r.target_price, 0) / reports.length) : 0
  );

  let currentPrice = $derived(data?.snapshot?.last_price ?? avgTarget);
  let upside = $derived(currentPrice ? ((avgTarget - currentPrice) / currentPrice) * 100 : 0);
  let upsideClass = $derived(upside >= 0 ? 'price-up' : 'price-down');

  // 투자의견 분포 (Buy/Hold/Sell). mock 기준 Buy 3 / Hold 1 / Sell 0.
  let buyCount = $derived(reports.filter((r) => r.investment_opinion === 'Buy').length);
  let holdCount = $derived(reports.filter((r) => r.investment_opinion === 'Hold').length);
  let sellCount = $derived(reports.filter((r) => r.investment_opinion === 'Sell').length);
  let totalCount = $derived(reports.length || 1);

  let dist = $derived([
    { key: 'Buy', label: '매수', count: buyCount, cls: 'opt-buy' },
    { key: 'Hold', label: '중립', count: holdCount, cls: 'opt-hold' },
    { key: 'Sell', label: '매도', count: sellCount, cls: 'opt-sell' }
  ]);

  // mock엔 현재 컨센서스만 존재 → 추이는 더미. 컴포넌트 로컬 const(최소).
  // 마지막 포인트는 실제 평균 목표주가(avgTarget)로 수렴.
  const TREND: { label: string; value: number }[] = [
    { label: '1월', value: 78000 },
    { label: '2월', value: 80000 },
    { label: '3월', value: 83000 },
    { label: '4월', value: 87000 },
    { label: '5월', value: 90000 }
  ];

  // 미니 SVG 라인차트 좌표 계산 (full LWC 아님).
  const VB_W = 280;
  const VB_H = 96;
  const PAD_X = 6;
  const PAD_Y = 10;

  let trendPts = $derived.by(() => {
    const series = TREND.map((t) => ({ ...t }));
    if (series.length) series[series.length - 1] = { ...series[series.length - 1], value: avgTarget };
    const vals = series.map((s) => s.value);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const stepX = (VB_W - PAD_X * 2) / (series.length - 1 || 1);
    return series.map((s, i) => ({
      ...s,
      x: PAD_X + stepX * i,
      y: PAD_Y + (VB_H - PAD_Y * 2) * (1 - (s.value - lo) / span)
    }));
  });

  let linePath = $derived(trendPts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '));
  let areaPath = $derived(
    trendPts.length
      ? `M ${trendPts[0].x.toFixed(1)},${VB_H} ` +
        trendPts.map((p) => `L ${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ') +
        ` L ${trendPts[trendPts.length - 1].x.toFixed(1)},${VB_H} Z`
      : ''
  );
  let lastPt = $derived(trendPts[trendPts.length - 1]);

  function optLabel(o: string): string {
    return o === 'Buy' ? '매수' : o === 'Hold' ? '중립' : o === 'Sell' ? '매도' : o;
  }
  function optClass(o: string): string {
    return o === 'Buy' ? 'rr-buy' : o === 'Sell' ? 'rr-sell' : 'rr-hold';
  }
</script>

<div class="reports-tab">
  <section class="panel rt-card">
    <div class="rt-head">
      <span class="rt-title">목표주가 컨센서스 추이</span>
      <div class="rt-target">
        <span class="rt-target-val tnum">{fmtPrice(avgTarget)}</span>
        <span class="rt-target-upside tnum {upsideClass}">
          {(upside >= 0 ? '+' : '') + upside.toFixed(1)}%
        </span>
      </div>
    </div>
    <div class="rt-sub">
      현재가 <span class="tnum">{fmtPrice(currentPrice)}</span> 대비 상승여력
    </div>

    <div class="trend-chart">
      <svg viewBox="0 0 {VB_W} {VB_H}" preserveAspectRatio="none" role="img" aria-label="목표주가 추이">
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--color-positive)" stop-opacity="0.22" />
            <stop offset="100%" stop-color="var(--color-positive)" stop-opacity="0" />
          </linearGradient>
        </defs>
        <path class="trend-area" d={areaPath} fill="url(#trendFill)" />
        <polyline class="trend-line" points={linePath} />
        {#if lastPt}
          <circle class="trend-dot" cx={lastPt.x} cy={lastPt.y} r="3.5" />
        {/if}
      </svg>
    </div>
    <div class="trend-axis">
      {#each trendPts as p}
        <span class="ta-tick">{p.label}</span>
      {/each}
    </div>
  </section>

  <section class="panel rt-card">
    <div class="rt-head">
      <span class="rt-title">투자의견 분포</span>
      <span class="rt-count tnum">{reports.length}개 리포트</span>
    </div>
    <div class="dist-list">
      {#each dist as d}
        <div class="dist-row">
          <span class="dist-label {d.cls}">{d.label}</span>
          <div class="dist-track">
            <div class="dist-fill {d.cls}" style="width: {(d.count / totalCount) * 100}%"></div>
          </div>
          <span class="dist-num tnum">{d.count}</span>
        </div>
      {/each}
    </div>
  </section>

  <section class="panel rt-card">
    <div class="rt-head">
      <span class="rt-title">증권사 리포트</span>
    </div>
    <div class="report-feed">
      {#each reports as r}
        <article class="report-item">
          <div class="ri-top">
            <span class="ri-provider">{r.provider}</span>
            <span class="ri-date tnum">{r.report_date}</span>
          </div>
          <h4 class="ri-title">{r.title}</h4>
          {#if r.author}
            <p class="ri-author">{r.author}</p>
          {/if}
          <div class="ri-foot">
            <span class="ri-opinion {optClass(r.investment_opinion)}">
              {optLabel(r.investment_opinion)}
            </span>
            <span class="ri-target">
              목표가 <span class="tnum">{fmtPrice(r.target_price)}</span>
            </span>
          </div>
        </article>
      {/each}
    </div>
  </section>
</div>

<style>
  .reports-tab {
    width: 100%;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3) 0;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  .rt-card {
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
  }

  .rt-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-2);
  }

  .rt-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  .rt-count {
    font-size: 12px;
    color: var(--text-tertiary);
  }

  .rt-target {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
  }
  .rt-target-val {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .rt-target-upside {
    font-size: 13px;
    font-weight: 600;
  }

  .rt-sub {
    font-size: 11px;
    color: var(--text-tertiary);
    margin-bottom: var(--space-3);
  }

  /* 추이 미니 차트 */
  .trend-chart {
    width: 100%;
    height: 96px;
  }
  .trend-chart svg {
    width: 100%;
    height: 100%;
    display: block;
  }
  .trend-line {
    fill: none;
    stroke: var(--color-positive);
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .trend-dot {
    fill: var(--color-positive);
    stroke: var(--surface-body);
    stroke-width: 2;
  }

  .trend-axis {
    display: flex;
    justify-content: space-between;
    margin-top: var(--space-2);
    padding: 0 2px;
  }
  .ta-tick {
    font-size: 10px;
    color: var(--text-tertiary);
  }

  /* 투자의견 분포 */
  .dist-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .dist-row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  .dist-label {
    font-size: 12px;
    font-weight: 600;
    width: 32px;
    flex-shrink: 0;
  }
  .dist-num {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    width: 16px;
    text-align: right;
    flex-shrink: 0;
  }
  .dist-track {
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background: var(--surface-overlay);
    overflow: hidden;
  }
  .dist-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s ease;
    min-width: 0;
  }

  .opt-buy { color: var(--color-positive); }
  .opt-buy.dist-fill { background: var(--color-positive); }
  .opt-hold { color: var(--text-secondary); }
  .opt-hold.dist-fill { background: var(--color-flat); }
  .opt-sell { color: var(--color-negative); }
  .opt-sell.dist-fill { background: var(--color-negative); }

  /* 리포트 리스트 */
  .report-feed {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .report-item {
    background: var(--surface-overlay);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: var(--space-3);
  }
  .ri-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: var(--space-2);
  }
  .ri-provider {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
  }
  .ri-date {
    font-size: 11px;
    color: var(--text-tertiary);
  }
  .ri-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0 0 var(--space-2);
    line-height: 1.4;
  }
  .ri-author {
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-tertiary);
    margin: 0 0 var(--space-3);
  }
  .ri-foot {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  .ri-opinion {
    font-size: 12px;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 999px;
  }
  .rr-buy { color: var(--color-positive); background: color-mix(in srgb, var(--color-positive) 12%, transparent); }
  .rr-hold { color: var(--text-secondary); background: var(--surface-raised); }
  .rr-sell { color: var(--color-negative); background: color-mix(in srgb, var(--color-negative) 12%, transparent); }
  .ri-target {
    font-size: 12px;
    color: var(--text-tertiary);
  }
  .ri-target .tnum {
    color: var(--text-primary);
    font-weight: 600;
  }
</style>
