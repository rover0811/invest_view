<script lang="ts">
  import type { Snapshot, TickDetail, ResolvedPrice } from './types';
  import { fmtPrice, fmtVol, pct, changeClass } from './format';

  let { meta, snapshot, tickDetail, resolved }: {
    meta: { symbol: string; stock_name: string };
    snapshot: Snapshot;
    tickDetail: TickDetail | null;
    resolved: ResolvedPrice | null;
  } = $props();

  // resolved is authoritative once loaded; before that we fall back to snapshot
  // so the first paint is not blank.
  const hasResolved = $derived(resolved != null);
  const isRealtime = $derived(resolved?.is_realtime === true);
  // Realtime-only fields (change/체결강도/VI) are only trustworthy when the
  // displayed price is live; on a daily-close fallback they are suppressed.
  const realtimeOk = $derived(!hasResolved || isRealtime);

  const priceVal = $derived(hasResolved ? resolved!.price : (snapshot?.last_price ?? null));
  const priceText = $derived(priceVal != null ? fmtPrice(priceVal) : '—');
  let flashClass = $state('');
  let previousPrice: number | null = null;
  let flashTimer: ReturnType<typeof setTimeout> | null = null;

  const effChange = $derived(hasResolved ? resolved!.change : (snapshot?.change ?? null));
  const effRate = $derived(hasResolved ? resolved!.change_rate : (snapshot?.change_rate ?? null));
  const up = $derived((effChange ?? 0) >= 0);
  const cls = $derived(realtimeOk ? changeClass(effChange ?? 0) : '');
  const sign = $derived(up ? '+' : '');
  const showChange = $derived(realtimeOk && effChange != null);
  const changeText = $derived(`${sign}${fmtPrice(effChange ?? 0)}  (${pct(effRate ?? 0)})`);

  const label = $derived(resolved?.display_label ?? '');
  const isDaily = $derived(resolved?.source === 'daily_close');
  const isNone = $derived(resolved?.source === 'none');
  const asOfText = $derived(isDaily && resolved?.as_of ? fmtAsOf(resolved.as_of) : '');

  // as_of is an ISO instant; the trade date must be read in KST (15:30 KST close).
  function fmtAsOf(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const parts = new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d);
    const y = parts.find((p) => p.type === 'year')?.value ?? '';
    const m = parts.find((p) => p.type === 'month')?.value ?? '';
    const day = parts.find((p) => p.type === 'day')?.value ?? '';
    return `${y}.${m}.${day} 종가`;
  }

  $effect(() => {
    const current = priceVal;
    if (current == null) {
      previousPrice = null;
      return;
    }
    if (previousPrice != null && current !== previousPrice) {
      flashClass = current > previousPrice ? 'sh-price--flash-up' : 'sh-price--flash-down';
      if (flashTimer != null) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        flashClass = '';
        flashTimer = null;
      }, 420);
    }
    previousPrice = current;
    return () => {
      if (flashTimer != null) clearTimeout(flashTimer);
    };
  });
</script>

<div class="stock-header">
  <div class="sh-left">
    <div class="sh-title">
      <span class="sh-name">{meta.stock_name}</span>
      <span class="sh-code tnum">{meta.symbol}</span>
    </div>
    <div class="sh-price-row">
      <span class="sh-price tnum {cls} {flashClass}">{priceText}</span>
      {#if showChange}
        <span class="sh-change tnum {cls}">{changeText}</span>
      {/if}
      {#if label}
        <span
          class="sh-badge"
          class:sh-badge--live={isRealtime}
          class:sh-badge--none={isNone}
          title={isDaily && asOfText ? asOfText : label}
        >
          {#if isRealtime}<span class="sh-dot" aria-hidden="true"></span>{/if}
          <span>{isDaily && asOfText ? asOfText : label}</span>
        </span>
      {/if}
    </div>
  </div>
  <div class="sh-right">
    <div class="sh-metrics" class:sh-metrics--stale={!realtimeOk}>
      <div class="sh-metric"><span class="sh-metric-label">체결강도</span><span class="sh-metric-val tnum">{realtimeOk && tickDetail?.trade_strength != null ? tickDetail.trade_strength.toFixed(1) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">거래량</span><span class="sh-metric-val tnum">{realtimeOk && snapshot?.cumulative_volume != null ? fmtVol(snapshot.cumulative_volume) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">VI 발동가</span><span class="sh-metric-val tnum">{realtimeOk && snapshot?.vi_trigger_price != null ? fmtPrice(snapshot.vi_trigger_price) : '—'}</span></div>
      <div class="sh-metric"><span class="sh-metric-label">VWAP</span><span class="sh-metric-val tnum">{realtimeOk && tickDetail?.vwap != null ? fmtPrice(tickDetail.vwap) : '—'}</span></div>
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
    position: relative;
    display: inline-block;
    font-size: 30px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -0.02em;
    /* color is the ONLY animated property on the glyph box; geometry is inert */
    transition: color 120ms ease;
  }

  /* Flash tint lives in an out-of-flow overlay so it can never reflow the
     text. Only its background-color animates; the price box never changes. */
  .sh-price::before {
    content: "";
    position: absolute;
    inset: -2px -4px;
    z-index: -1;
    border-radius: var(--radius-sm);
    background-color: transparent;
    transition: background-color 420ms ease;
    pointer-events: none;
  }

  .sh-price--flash-up {
    color: var(--color-positive);
  }

  .sh-price--flash-up::before {
    background-color: color-mix(in srgb, var(--color-positive) 14%, transparent);
  }

  .sh-price--flash-down {
    color: var(--color-negative);
  }

  .sh-price--flash-down::before {
    background-color: color-mix(in srgb, var(--color-negative) 14%, transparent);
  }

  .sh-change {
    font-size: 14px;
    font-weight: 600;
  }

  .sh-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    font-size: 11px;
    font-weight: 600;
    line-height: 1.4;
    color: var(--text-tertiary);
    white-space: nowrap;
    align-self: center;
  }

  .sh-badge--live {
    color: var(--color-positive);
  }

  .sh-badge--none {
    color: var(--text-tertiary);
  }

  .sh-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-positive);
    animation: sh-pulse 1.6s var(--ease-out) infinite;
  }

  @keyframes sh-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .sh-metrics--stale {
    opacity: 0.45;
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
