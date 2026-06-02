<script lang="ts">
  import type { IndexItem, StockListItem } from './types';
  import { getIndices, getStockList } from './api';
  import { fmtPrice, pct, changeClass } from './format';

  let { onNavigate }: { onNavigate: (path: string) => void } = $props();

  let indices = $state<IndexItem[]>([]);
  let stocks = $state<StockListItem[]>([]);

  $effect(() => {
    getIndices().then((d) => (indices = d)).catch(() => (indices = []));
    getStockList().then((d) => (stocks = d)).catch(() => (stocks = []));
  });

  // 미니 스파크라인: 값 배열을 0~100 viewBox 좌표로 정규화한 polyline points
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
</script>

<div class="home">
  <section class="index-strip">
    {#each indices as ix}
      <div class="index-card">
        <div class="ix-top">
          <span class="ix-name">{ix.name}</span>
          <svg class="ix-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
            <polyline
              points={sparkPoints(ix.sparkline)}
              fill="none"
              stroke="var({ix.change_rate >= 0 ? '--color-positive' : '--color-negative'})"
              stroke-width="1.5"
              vector-effect="non-scaling-stroke"
              stroke-linejoin="round"
              stroke-linecap="round"
            />
          </svg>
        </div>
        <div class="ix-value tnum">{fmtPrice(ix.value)}</div>
        <div class="ix-change tnum {changeClass(ix.change_rate)}">
          {pct(ix.change_rate * 100)}
        </div>
      </div>
    {/each}
  </section>

  <section class="stock-section">
    <div class="sec-head">
      <span class="sec-title">실시간 인기·거래상위</span>
      <span class="sec-sub">{stocks.length}종목</span>
    </div>
    <div class="stock-list">
      {#each stocks as s, i}
        <button class="stock-row" onclick={() => onNavigate('/stocks/' + s.code)}>
          <span class="sr-rank tnum">{i + 1}</span>
          <span class="sr-id">
            <span class="sr-name">{s.name}</span>
            <span class="sr-code">{s.code} · {s.market}</span>
          </span>
          <span class="sr-price tnum">{fmtPrice(s.price)}</span>
          <span class="sr-change tnum {changeClass(s.change_rate)}">{pct(s.change_rate * 100)}</span>
        </button>
      {/each}
    </div>
  </section>
</div>

<style>
  .home {
    width: 100%;
    max-width: 1320px;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--space-4) 0;
  }

  /* 지수 띠 */
  .index-strip {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: var(--space-3);
  }

  .index-card {
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-3) var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .ix-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }

  .ix-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .ix-spark {
    width: 52px;
    height: 22px;
    flex-shrink: 0;
  }

  .ix-value {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
  }

  .ix-change {
    font-size: 12px;
    font-weight: 600;
  }

  /* 종목 리스트 */
  .stock-section {
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
  }

  .sec-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: var(--space-3);
  }

  .sec-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .sec-sub {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .stock-list {
    display: flex;
    flex-direction: column;
  }

  .stock-row {
    display: grid;
    grid-template-columns: 28px 1fr auto auto;
    align-items: center;
    gap: var(--space-3);
    width: 100%;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border-subtle);
    padding: var(--space-3) var(--space-2);
    cursor: pointer;
    text-align: left;
    color: inherit;
    font-family: inherit;
    transition: background 0.12s ease;
  }

  .stock-row:last-child {
    border-bottom: none;
  }

  .stock-row:hover {
    background: var(--surface-raised);
  }

  .sr-rank {
    font-size: 13px;
    font-weight: 700;
    color: var(--text-tertiary);
    text-align: center;
  }

  .sr-id {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .sr-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .sr-code {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .sr-price {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    text-align: right;
  }

  .sr-change {
    font-size: 13px;
    font-weight: 600;
    text-align: right;
    min-width: 64px;
  }
</style>
