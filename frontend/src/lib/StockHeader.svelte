<script lang="ts">
  import type { Snapshot, TickDetail } from './types';
  import { fmtPrice, fmtVol, pct, changeClass } from './format';

  let { meta, snapshot, tickDetail }: { 
    meta: { symbol: string; stock_name: string }; 
    snapshot: Snapshot;
    tickDetail: TickDetail;
  } = $props();

  const up = $derived((snapshot.change ?? 0) >= 0);
  const cls = $derived(changeClass(snapshot.change ?? 0));
  const sign = $derived(up ? '+' : '');
  
  const priceText = $derived(fmtPrice(snapshot.last_price ?? 0));
  const changeText = $derived(`${sign}${fmtPrice(snapshot.change ?? 0)}  (${pct((snapshot.change_rate ?? 0) * 100)})`);
</script>

<div class="stock-header">
  <div class="sh-left">
    <div class="sh-title">
      <span class="sh-name">{meta.stock_name}</span>
      <span class="sh-code tnum">{meta.symbol}</span>
    </div>
    <div class="sh-price-row">
      <span class="sh-price tnum {cls}">{priceText}</span>
      <span class="sh-change tnum {cls}">{changeText}</span>
    </div>
  </div>
  <div class="sh-right">
    <div class="sh-metrics">
      <div class="sh-metric"><span class="sh-metric-label">체결강도</span><span class="sh-metric-val tnum">{tickDetail?.trade_strength != null ? tickDetail.trade_strength.toFixed(1) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">거래량</span><span class="sh-metric-val tnum">{snapshot?.cumulative_volume != null ? fmtVol(snapshot.cumulative_volume) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">VI 발동가</span><span class="sh-metric-val tnum">{snapshot?.vi_trigger_price != null ? fmtPrice(snapshot.vi_trigger_price) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">VWAP</span><span class="sh-metric-val tnum">{tickDetail?.vwap != null ? fmtPrice(tickDetail.vwap) : '—'}</span></div>
    </div>
  </div>
</div>

<style>
  .stock-header {
    width: 100%;
    max-width: 1320px;
    box-sizing: border-box;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    padding: var(--space-2) var(--space-3) var(--space-3);
    margin-bottom: var(--space-2);
  }

  .sh-title {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    margin-bottom: var(--space-2);
  }

  .sh-name {
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .sh-code {
    font-size: 13px;
    color: var(--text-tertiary);
  }

  .sh-price-row {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }

  .sh-price {
    font-size: 30px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -0.02em;
  }

  .sh-change {
    font-size: 14px;
    font-weight: 600;
  }

  .sh-metrics {
    display: grid;
    grid-template-columns: repeat(4, auto);
    gap: var(--space-2) var(--space-4);
  }

  .sh-metric {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
  }

  .sh-metric-label {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .sh-metric-val {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
  }
</style>
