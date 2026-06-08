<script lang="ts">
  import type { StockData, ResolvedPrice } from './types';
  import { getStockData, getSnapshot, getTickDetail, streamPrice } from './api';
  import { appState } from './stores.svelte';
  import StockHeader from './StockHeader.svelte';
  import TabBar from './TabBar.svelte';
  import ChartView from './ChartView.svelte';
  import StockInfo from './StockInfo.svelte';
  import EventsTab from './EventsTab.svelte';
  import ReportsTab from './ReportsTab.svelte';
  import AiPanel from './AiPanel.svelte';

  let { code, tab }: { code: string; tab: string } = $props();

  const DETAIL_POLL_MS = 5000;

  let data = $state<StockData | null>(null);
  // Authoritative price+provenance for the header (live vs stale daily-close).
  let resolved = $state<ResolvedPrice | null>(null);
  // When the AI panel goes full-width it hides the left column (header + tabbar + chart).
  let aiFullWidth = $state(false);

  $effect(() => {
    // reference code so the effect re-runs when it changes
    const c = code;
    getStockData(c).then((d) => {
      data = d;
    });
  });

  $effect(() => {
    const c = code;
    let stopped = false;
    let hidden = document.hidden;
    let streamAbort: AbortController | null = null;
    resolved = null;

    function patchSnapshotFromPrice(price: ResolvedPrice) {
      if (data == null || price.symbol !== c) return;
      if (price.price != null) data.snapshot.last_price = price.price;
      if (price.change != null) data.snapshot.change = price.change;
      if (price.change_rate != null) data.snapshot.change_rate = price.change_rate;
      if (price.change_sign != null) data.snapshot.change_sign = price.change_sign;
      if (price.cumulative_volume != null) data.snapshot.cumulative_volume = price.cumulative_volume;
      if (price.vi_trigger_price != null) data.snapshot.vi_trigger_price = price.vi_trigger_price;
      if (price.trading_halted != null) data.snapshot.trading_halted = price.trading_halted;
      if (price.as_of != null) data.snapshot.updated_at = price.as_of;
    }

    function openPriceStream() {
      if (stopped || hidden) return;
      streamAbort?.abort();
      streamAbort = new AbortController();
      void streamPrice(
        c,
        {
          onPrice: (price) => {
            if (stopped || hidden || c !== code) return;
            resolved = price;
            patchSnapshotFromPrice(price);
          },
          onError: (message) => {
            if (!stopped) console.warn(message);
          },
        },
        streamAbort.signal,
      );
    }

    async function refreshDetails() {
      // document.hidden: skip background tabs to avoid pointless churn.
      if (stopped || document.hidden) return;
      const [snapshot, tickDetail] = await Promise.all([
        getSnapshot(c).catch(() => null),
        getTickDetail(c).catch(() => null),
      ]);
      // Stale-response guard: code may have changed mid-flight; only patch if
      // this effect run still owns the current symbol and was not torn down.
      if (stopped || c !== code) return;
      if (data == null || snapshot == null) return;
      // In-place field mutation (data is a $state proxy) so ONLY the snapshot/
      // tickDetail signals fire. Reassigning the whole object (`{ ...data }`) made
      // Svelte 5 re-notify EVERY prop signal — incl. `timeline`/`candles` whose
      // array refs are unchanged — which re-fired Chart's render effect every 5s,
      // destroying + recreating the chart (fitContent) and wiping the user's zoom/pan.
      data.snapshot = snapshot;
      data.tickDetail = tickDetail;
    }

    function onVisibilityChange() {
      hidden = document.hidden;
      if (hidden) {
        streamAbort?.abort();
        streamAbort = null;
      } else {
        openPriceStream();
        void refreshDetails();
      }
    }

    openPriceStream();
    refreshDetails();
    document.addEventListener('visibilitychange', onVisibilityChange);
    const timer = setInterval(refreshDetails, DETAIL_POLL_MS);
    return () => {
      stopped = true;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      streamAbort?.abort();
      clearInterval(timer);
    };
  });

  function selectTab(newTab: string) {
    appState.route.tab = newTab;
    window.location.hash = '/stocks/' + code + '?tab=' + newTab;
  }
</script>

<div class="stock-detail">
  {#if data != null}
    {#if tab === 'chart'}
      <div class="detail-chart-layout">
        <div class="detail-left" class:hidden={aiFullWidth}>
          <StockHeader meta={data._meta} snapshot={data.snapshot} tickDetail={data.tickDetail} {resolved} />
          <TabBar activeTab={tab} onSelect={selectTab} />
          <div class="detail-left-content">
            <ChartView {data} />
          </div>
        </div>
        <AiPanel {data} onFullWidthChange={(v) => (aiFullWidth = v)} />
      </div>
    {:else}
      <StockHeader meta={data._meta} snapshot={data.snapshot} tickDetail={data.tickDetail} {resolved} />
      <TabBar activeTab={tab} onSelect={selectTab} />
      <div class="tab-content">
        {#if tab === 'info'}
          <StockInfo {data} section={appState.route.section} />
        {:else if tab === 'events'}
          <EventsTab {data} />
        {:else}
          <ReportsTab {data} />
        {/if}
      </div>
    {/if}
  {/if}
</div>

<style>
  .stock-detail {
    width: 100%;
    max-width: 1320px;
    margin: 0 auto;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1;
  }

  .tab-content {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    padding-top: var(--space-3);
  }

  .detail-chart-layout {
    display: flex;
    flex: 1;
    min-height: 0;
    gap: var(--space-3);
    align-items: stretch;
  }

  .detail-left {
    flex: 1;
    min-width: 0;
    min-height: 0;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }

  .detail-left.hidden {
    display: none;
  }

  .detail-left-content {
    flex: 1;
    min-height: 0;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-gutter: stable;
    padding-right: 4px;
    padding-top: var(--space-3);
  }

  .detail-left-content :global(.chart-panel) {
    flex-shrink: 0;
    box-sizing: border-box;
  }

  .detail-left-content :global(.metrics-grid) {
    flex-shrink: 0;
    box-sizing: border-box;
  }
</style>
