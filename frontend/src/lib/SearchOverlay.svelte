<script lang="ts">
  import { getStockList } from './api';
  import type { StockListItem } from './types';
  import { fmtPrice, pct, changeClass } from './format';

  let { open, onClose, onNavigate }: { open: boolean; onClose: () => void; onNavigate: (path: string) => void } = $props();

  let stocks = $state<StockListItem[]>([]);
  let loading = $state(false);

  $effect(() => {
    if (open && stocks.length === 0) {
      loading = true;
      getStockList().then(data => {
        stocks = data;
        loading = false;
      }).catch(err => {
        console.error(err);
        loading = false;
      });
    }
  });

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      onClose();
    }
  }

  $effect(() => {
    if (open) {
      window.addEventListener('keydown', handleKeydown);
      return () => window.removeEventListener('keydown', handleKeydown);
    }
  });
</script>

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="backdrop" onclick={onClose}>
    <div class="overlay panel" onclick={(e) => e.stopPropagation()}>
      <div class="header">
        <h3>최근 검색</h3>
        <button class="close-btn" onclick={onClose}>✕</button>
      </div>
      
      {#if loading}
        <div class="loading">로딩 중...</div>
      {:else}
        <ul class="stock-list">
          {#each stocks as stock}
            <li>
              <button class="stock-item" onclick={() => { onNavigate(`/stocks/${stock.code}`); onClose(); }}>
                <div class="left">
                  <span class="name">{stock.name}</span>
                  <span class="code">{stock.code}</span>
                </div>
                <div class="right">
                  <span class="price tnum">{fmtPrice(stock.price)}원</span>
                  <span class="change tnum {changeClass(stock.change_rate)}">
                    {pct(stock.change_rate)}
                  </span>
                  <span class="market">{stock.market}</span>
                </div>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    top: 60px; /* Below GNB */
    left: 0;
    right: 0;
    bottom: 0;
    background: var(--overlay-scrim);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    z-index: 100;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding-top: var(--space-4);
  }

  .overlay {
    width: 100%;
    max-width: 600px;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: var(--shadow-overlay);
  }

  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-4) var(--space-6);
    border-bottom: 1px solid var(--border-subtle);
  }

  .header h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 18px;
    cursor: pointer;
    padding: 0;
  }

  .close-btn:hover {
    color: var(--text-primary);
  }

  .loading {
    padding: var(--space-6);
    text-align: center;
    color: var(--text-secondary);
  }

  .stock-list {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow-y: auto;
  }

  .stock-item {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-3) var(--space-6);
    background: none;
    border: none;
    border-bottom: 1px solid var(--border-subtle);
    cursor: pointer;
    text-align: left;
    transition: background 0.2s;
  }

  .stock-item:hover {
    background: var(--surface-raised);
  }

  .left {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .name {
    font-weight: 600;
    color: var(--text-primary);
    font-size: 15px;
  }

  .code {
    color: var(--text-tertiary);
    font-size: 13px;
    font-family: var(--font-mono);
  }

  .right {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .price {
    color: var(--text-primary);
    font-size: 15px;
    font-weight: 500;
  }

  .change {
    font-size: 14px;
    font-weight: 500;
    min-width: 60px;
    text-align: right;
  }

  .market {
    font-size: 11px;
    color: var(--text-tertiary);
    background: var(--surface-floor);
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid var(--border-subtle);
  }
</style>