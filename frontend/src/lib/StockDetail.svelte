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

  let { code, tab }: { code: string; tab: string } = $props();

  let data = $state<StockData | null>(null);

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
    <StockHeader meta={data._meta} snapshot={data.snapshot} tickDetail={data.tickDetail} />
    <TabBar activeTab={tab} onSelect={selectTab} />
    <div class="tab-content">
      {#if tab === 'chart'}
        <ChartView {data} />
      {:else if tab === 'info'}
        <StockInfo {data} section={appState.route.section} />
      {:else if tab === 'events'}
        <EventsTab {data} />
      {:else}
        <ReportsTab {data} />
      {/if}
    </div>
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
</style>
