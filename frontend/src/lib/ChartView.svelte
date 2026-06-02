<script lang="ts">
  import type { StockData } from './types';
  import Chart from './Chart.svelte';
  import MetricsGrid from './MetricsGrid.svelte';
  import AiPanel from './AiPanel.svelte';

  let { data }: { data: StockData } = $props();
</script>

<div class="main-row">
  <div class="main-left">
    <Chart candles={data.candles} timeline={data.timeline} snapshot={data.snapshot} />
    <MetricsGrid
      tickDetail={data.tickDetail}
      indicators={data.indicators}
      consensus={data.consensus}
      fundamentals={data.fundamentals}
      snapshot={data.snapshot}
    />
  </div>
  <AiPanel {data} />
</div>

<style>
  .main-row {
    width: 100%;
    max-width: 1320px;
    box-sizing: border-box;
    display: flex;
    gap: var(--space-3);
    align-items: stretch;
    flex: 1;
    min-height: 0;
  }

  .main-left {
    flex: 1;
    min-width: 0;
    min-height: 0;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-gutter: stable;
    padding-right: 4px;
  }

  .main-left :global(.chart-panel) {
    flex-shrink: 0;
    box-sizing: border-box;
  }

  .main-left :global(.metrics-grid) {
    flex-shrink: 0;
    box-sizing: border-box;
  }
</style>
