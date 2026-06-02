<script lang="ts">
  import type { StockData } from './types';

  let { data, section }: { data: StockData; section?: string } = $props();

  let f = $derived(data.fundamentals);
  let ind = $derived(data.indicators);

  let highlighted = $state<string | null>(null);
  $effect(() => {
    if (!section) return;
    const id = 'section-' + section;
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      highlighted = id;
      setTimeout(() => { if (highlighted === id) highlighted = null; }, 1400);
    });
  });

  function fmtMarketCap(n: number): string {
    if (n == null) return '—';
    const jo = n / 1e12;
    return jo.toFixed(1) + '조';
  }

  function fmtListingDate(s: string): string {
    if (!s) return '—';
    return s.replace(/-/g, '.');
  }

  let overview = $derived([
    { label: '시가총액', value: fmtMarketCap(f?.market_cap) },
    { label: '상장일', value: fmtListingDate(f?.listing_date) },
    { label: '대표이사', value: f?.ceo_name ?? '—' },
    { label: '업종', value: f?.industry_name ?? '—' }
  ]);

  let indList = $derived([
    { label: 'PER (이익 대비)', value: ind?.per, suffix: '배' },
    { label: 'PBR (자산 대비)', value: ind?.pbr, suffix: '배' },
    { label: 'ROE (자본 효율)', value: ind?.roe, suffix: '%' },
    { label: 'EPS (주당순이익)', value: ind?.eps, suffix: '원', whole: true },
    { label: 'BPS (주당순자산)', value: ind?.bps, suffix: '원', whole: true },
    { label: '배당수익률', value: ind?.dividend_yield, suffix: '%' }
  ]);

  let finSummaries = $derived([
    { t: '재무상태', x: f?.balance_sheet_summary },
    { t: '손익', x: f?.income_statement_summary },
    { t: '현금흐름', x: f?.cash_flow_summary }
  ].filter(s => s.x));

  // DUMMY: quarterly revenue (조원) — 수급/재무 시계열은 mock에 없음
  const quarterlyRevenue: { q: string; v: number }[] = [
    { q: '25 Q2', v: 67.4 },
    { q: '25 Q3', v: 71.2 },
    { q: '25 Q4', v: 75.8 },
    { q: '26 Q1', v: 79.1 }
  ];
  const revMax = Math.max(...quarterlyRevenue.map(r => r.v));

  // DUMMY: 수급 순매수 (억원) — 3 actors × 5 days. 한국 관례: 순매수(+)=빨강, 순매도(-)=파랑
  const flowDays = ['5/26', '5/27', '5/28', '5/29', '5/30'];
  const flows: { actor: string; values: number[] }[] = [
    { actor: '외국인', values: [320, -150, 480, 210, -90] },
    { actor: '기관', values: [-210, 340, -120, 80, 150] },
    { actor: '개인', values: [-110, -190, -360, -290, -60] }
  ];
  const flowMax = Math.max(...flows.flatMap(a => a.values.map(v => Math.abs(v))));

  function fmtFlow(v: number): string {
    return (v >= 0 ? '+' : '') + v.toLocaleString('ko-KR');
  }
</script>

<div class="stock-info">
  <section id="section-overview" class="info-card" class:flash={highlighted === 'section-overview'}>
    <div class="ic-head"><span class="ic-title">기업개요</span></div>
    <div class="overview-grid">
      {#each overview as o}
        <div class="ov-row">
          <span class="ov-label">{o.label}</span>
          <span class="ov-value">{o.value}</span>
        </div>
      {/each}
    </div>
  </section>

  <section id="section-indicators" class="info-card" class:flash={highlighted === 'section-indicators'}>
    <div class="ic-head"><span class="ic-title">투자지표</span></div>
    <div class="indicator-grid">
      {#each indList as i}
        <div class="ind-cell">
          <span class="ind-label">{i.label}</span>
          <span class="ind-value tnum">
            {#if i.value == null}
              —
            {:else}
              {i.whole ? Math.round(i.value).toLocaleString('ko-KR') : i.value.toFixed(2)}<small>{i.suffix}</small>
            {/if}
          </span>
        </div>
      {/each}
    </div>
  </section>

  <section id="section-financials" class="info-card" class:flash={highlighted === 'section-financials'}>
    <div class="ic-head">
      <span class="ic-title">재무</span>
      <span class="ic-sub">분기 매출 (조원)</span>
    </div>
    <div class="rev-chart">
      {#each quarterlyRevenue as r}
        <div class="rev-col">
          <span class="rev-val tnum">{r.v.toFixed(1)}</span>
          <div class="rev-bar" style="height: {(r.v / revMax) * 100}%"></div>
          <span class="rev-q">{r.q}</span>
        </div>
      {/each}
    </div>
    <div class="fin-summaries">
      {#each finSummaries as s}
        <div class="fin-section">
          <div class="fin-section-title">{s.t}</div>
          <div class="fin-section-text">{s.x}</div>
        </div>
      {/each}
    </div>
  </section>

  <section id="section-flow" class="info-card" class:flash={highlighted === 'section-flow'}>
    <div class="ic-head">
      <span class="ic-title">수급</span>
      <span class="ic-sub">최근 5일 순매수 (억원)</span>
    </div>
    <div class="flow-list">
      {#each flows as a}
        <div class="flow-actor">
          <span class="flow-name">{a.actor}</span>
          <div class="flow-bars">
            {#each a.values as v, di}
              <div class="flow-col">
                <div class="flow-track">
                  <div
                    class="flow-bar {v >= 0 ? 'price-up' : 'price-down'}"
                    class:pos={v >= 0}
                    class:neg={v < 0}
                    style="height: {(Math.abs(v) / flowMax) * 50}%"
                  ></div>
                </div>
                <span class="flow-day">{flowDays[di]}</span>
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>
    <div class="flow-legend">
      <span class="leg-item"><span class="leg-dot up"></span>순매수</span>
      <span class="leg-item"><span class="leg-dot down"></span>순매도</span>
    </div>
  </section>
</div>

<style>
  .stock-info {
    width: 100%;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3) 0;
    overflow-y: auto;
  }

  .info-card {
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    scroll-margin-top: var(--space-4);
  }

  .info-card.flash {
    animation: section-flash var(--dur-fill) var(--ease-out);
  }

  @keyframes section-flash {
    0% { border-color: var(--brand); background: var(--surface-overlay); }
    100% { border-color: var(--border-subtle); background: var(--surface-body); }
  }

  .ic-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: var(--space-4);
  }

  .ic-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .ic-sub {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  /* 기업개요 */
  .overview-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-4) var(--space-6);
  }

  .ov-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--space-3);
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: var(--space-2);
  }

  .ov-label { font-size: 12px; color: var(--text-tertiary); }
  .ov-value {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    text-align: right;
  }

  /* 투자지표 */
  .indicator-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-4) var(--space-3);
  }

  .ind-cell { display: flex; flex-direction: column; gap: 4px; }
  .ind-label { font-size: 11px; color: var(--text-tertiary); }
  .ind-value { font-size: 17px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.02em; }
  .ind-value small { font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-left: 1px; }

  /* 재무 - 분기 매출 차트 */
  .rev-chart {
    display: flex;
    align-items: flex-end;
    justify-content: space-around;
    gap: var(--space-3);
    height: 120px;
    margin-bottom: var(--space-4);
    padding-top: var(--space-3);
  }

  .rev-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    height: 100%;
    gap: var(--space-1);
  }

  .rev-val { font-size: 11px; font-weight: 600; color: var(--text-secondary); }
  .rev-bar {
    width: 60%;
    max-width: 40px;
    background: var(--brand);
    border-radius: var(--radius-sm) var(--radius-sm) 0 0;
    min-height: 4px;
  }
  .rev-q { font-size: 11px; color: var(--text-tertiary); }

  .fin-summaries {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    background: var(--surface-overlay);
    border-radius: var(--radius-sm);
    padding: var(--space-3);
  }
  .fin-section-title { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 4px; }
  .fin-section-text { font-size: 12px; line-height: 1.6; color: var(--text-secondary); }

  /* 수급 */
  .flow-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .flow-actor {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .flow-name {
    flex: 0 0 48px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  .flow-bars {
    flex: 1;
    display: flex;
    justify-content: space-around;
    gap: var(--space-2);
  }

  .flow-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-1);
  }

  .flow-track {
    position: relative;
    width: 100%;
    height: 56px;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }

  .flow-track::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    height: 1px;
    background: var(--border-subtle);
  }

  .flow-bar {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    width: 60%;
    max-width: 22px;
    min-height: 3px;
    border-radius: 2px;
  }
  .flow-bar.pos { bottom: 50%; }
  .flow-bar.neg { top: 50%; }
  .flow-bar.price-up { background: var(--color-positive); }
  .flow-bar.price-down { background: var(--color-negative); }

  .flow-day { font-size: 10px; color: var(--text-tertiary); }

  .flow-legend {
    display: flex;
    gap: var(--space-4);
    margin-top: var(--space-4);
    padding-top: var(--space-3);
    border-top: 1px solid var(--border-subtle);
  }
  .leg-item {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 11px;
    color: var(--text-tertiary);
  }
  .leg-dot { width: 8px; height: 8px; border-radius: 2px; }
  .leg-dot.up { background: var(--color-positive); }
  .leg-dot.down { background: var(--color-negative); }
</style>
