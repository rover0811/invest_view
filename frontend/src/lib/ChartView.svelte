<script lang="ts">
  import type { StockData } from './types';
  import Chart from './Chart.svelte';
  import MetricsGrid from './MetricsGrid.svelte';

  let { data }: { data: StockData } = $props();
</script>

<Chart candles={data.candles} timeline={data.timeline} snapshot={data.snapshot} symbol={data._meta.symbol} />
<MetricsGrid
  tickDetail={data.tickDetail}
  indicators={data.indicators}
  consensus={data.consensus}
  snapshot={data.snapshot}
/>
