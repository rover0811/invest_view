<script lang="ts">
  // biome-ignore lint/correctness/noUnusedImports: component is consumed by the Svelte template
  import Gnb from './lib/Gnb.svelte';
  // biome-ignore lint/correctness/noUnusedImports: component is consumed by the Svelte template
  import Home from './lib/Home.svelte';
  // biome-ignore lint/correctness/noUnusedImports: component is consumed by the Svelte template
  import StockDetail from './lib/StockDetail.svelte';
  // biome-ignore lint/correctness/noUnusedImports: state and navigation are consumed by the Svelte template
  import { appState, initRouter, navigate } from './lib/stores.svelte';

  $effect(() => {
    initRouter();
  });
</script>

<div class="app-shell">
  <Gnb onNavigate={navigate} />

  <main class="content-shell">
    {#if appState.route.view === 'home'}
      <Home onNavigate={navigate} />
    {:else if appState.route.code != null}
      <StockDetail code={appState.route.code} tab={appState.route.tab ?? 'chart'} />
    {:else}
      <Home onNavigate={navigate} />
    {/if}
  </main>
</div>

<style>
  .app-shell {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--surface-floor);
  }

  .content-shell {
    flex: 1;
    min-height: 0;
    box-sizing: border-box;
    display: flex;
    justify-content: center;
    overflow: hidden;
    padding: calc(60px + var(--space-4)) var(--space-6) var(--space-4);
  }
</style>
