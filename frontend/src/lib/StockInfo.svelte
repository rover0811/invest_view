<script lang="ts">
  import type { StockData, FinancialLine } from './types';

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

  function fmtMarketCap(n: number | null): string {
    if (n == null) return '—';
    const jo = n / 1e12;
    return jo.toFixed(1) + '조';
  }

  function fmtListingDate(s: string | null): string {
    if (!s) return '—';
    return s.replace(/-/g, '.');
  }

  let overview = $derived([
    { label: '시가총액', value: fmtMarketCap(f?.market_cap ?? null) },
    { label: '상장일', value: fmtListingDate(f?.listing_date ?? null) },
    { label: '대표이사', value: f?.ceo_name ?? '—' },
    { label: '업종', value: f?.industry_name ?? '—' }
  ]);

  let indList = $derived([
    { label: 'PER (이익 대비)', value: ind?.per, suffix: '배' },
    { label: 'PBR (자산 대비)', value: ind?.pbr, suffix: '배' },
    { label: 'EPS (주당순이익)', value: ind?.eps, suffix: '원', whole: true }
  ]);

  const FIN_TABS: { key: 'income' | 'balance' | 'cashflow'; label: string }[] = [
    { key: 'income', label: '손익' },
    { key: 'balance', label: '재무상태' },
    { key: 'cashflow', label: '현금흐름' }
  ];
  let activeFin = $state<'income' | 'balance' | 'cashflow'>('income');

  // financials are long-format (item × period). Pivot to a table: distinct periods
  // become columns (newest first), distinct items become rows, value indexed by both.
  let finTable = $derived.by(() => {
    const lines: FinancialLine[] = f?.financials?.[activeFin] ?? [];
    const periods = [...new Set(lines.map(l => l.period))].sort((a, b) => b.localeCompare(a));
    const items: string[] = [];
    const byItemPeriod = new Map<string, FinancialLine>();
    for (const l of lines) {
      if (!items.includes(l.item)) items.push(l.item);
      byItemPeriod.set(l.item + '\u0000' + l.period, l);
    }
    const rows = items.map(item => ({
      item,
      cells: periods.map(p => byItemPeriod.get(item + '\u0000' + p) ?? null)
    }));
    return { periods, rows };
  });

  function fmtFinValue(line: FinancialLine | null): string {
    if (line == null || line.value == null) return '—';
    return Math.round(line.value).toLocaleString('ko-KR');
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
      <span class="ic-title">재무제표</span>
      <div class="fin-tabs">
        {#each FIN_TABS as t}
          <button
            type="button"
            class="fin-tab"
            class:active={activeFin === t.key}
            onclick={() => activeFin = t.key}
          >{t.label}</button>
        {/each}
      </div>
    </div>
    {#if finTable.rows.length > 0}
      <div class="fin-table-wrap">
        <table class="fin-table">
          <thead>
            <tr>
              <th class="ft-item">항목</th>
              {#each finTable.periods as p}
                <th class="ft-period tnum">{p}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each finTable.rows as row}
              <tr>
                <td class="ft-item">{row.item}</td>
                {#each row.cells as cell}
                  <td class="ft-value tnum">{fmtFinValue(cell)}</td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <div class="fin-unit-note">단위: 백만원 (KRW)</div>
    {:else}
      <div class="fin-empty">재무제표 데이터가 아직 없습니다.</div>
    {/if}
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

  /* 재무제표 표 */
  .fin-tabs {
    display: flex;
    gap: var(--space-1);
  }
  .fin-tab {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-tertiary);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    padding: 3px 10px;
    cursor: pointer;
    font-family: inherit;
    transition: color var(--dur-hover) var(--ease-out),
                background var(--dur-hover) var(--ease-out);
  }
  .fin-tab:hover { color: var(--text-secondary); background: var(--surface-overlay); }
  .fin-tab.active {
    color: var(--text-primary);
    background: var(--surface-overlay);
    border-color: var(--border-subtle);
  }

  .fin-table-wrap {
    width: 100%;
    overflow-x: auto;
  }
  .fin-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .fin-table th,
  .fin-table td {
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-subtle);
    white-space: nowrap;
  }
  .fin-table thead th {
    font-weight: 600;
    color: var(--text-tertiary);
    text-align: right;
    background: var(--surface-overlay);
  }
  .fin-table th.ft-item,
  .fin-table td.ft-item {
    text-align: left;
    position: sticky;
    left: 0;
    background: var(--surface-body);
    color: var(--text-secondary);
  }
  .fin-table thead th.ft-item {
    background: var(--surface-overlay);
  }
  .fin-table td.ft-value {
    text-align: right;
    color: var(--text-primary);
    font-weight: 500;
  }
  .fin-table tbody tr:hover td { background: var(--surface-overlay); }

  .fin-unit-note {
    font-size: 11px;
    color: var(--text-tertiary);
    margin-top: var(--space-2);
    text-align: right;
  }
  .fin-empty {
    font-size: 12px;
    color: var(--text-tertiary);
    padding: var(--space-4) 0;
    text-align: center;
  }
</style>
