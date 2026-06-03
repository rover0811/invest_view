<script lang="ts">
  import type { StockData } from './types';
  import { getStockData } from './api';
  import { appState } from './stores.svelte';
  import StockHeader from './StockHeader.svelte';
  import TabBar from './TabBar.svelte';
  import ChartView from './ChartView.svelte';
  import StockInfo from './StockInfo.svelte';
  import EventsTab from './EventsTab.svelte';
  import ReportsTab from './ReportsTab.svelte';
  import AiPanel from './AiPanel.svelte';

  let { code, tab }: { code: string; tab: string } = $props();

  let data = $state<StockData | null>(null);
  // When the AI panel goes full-width it hides the left column (header + tabbar + chart).
  let aiFullWidth = $state(false);

  $effect(() => {
    // reference code so the effect re-runs when it changes
    const c = code;
    getStockData(c).then((d) => {
      data = d;
    });
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
          <StockHeader meta={data._meta} snapshot={data.snapshot} tickDetail={data.tickDetail} />
          <TabBar activeTab={tab} onSelect={selectTab} />
          <div class="detail-left-content">
            <ChartView {data} />
          </div>
        </div>
        <AiPanel {data} onFullWidthChange={(v) => (aiFullWidth = v)} />
      </div>
    {:else}
      <StockHeader meta={data._meta} snapshot={data.snapshot} tickDetail={data.tickDetail} />
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
