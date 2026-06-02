<script lang="ts">
  let { activeTab, onSelect }: { activeTab: string; onSelect: (tab: string) => void } = $props();

  const tabs: { key: string; label: string }[] = [
    { key: 'chart', label: '차트' },
    { key: 'info', label: '종목정보' },
    { key: 'events', label: '이벤트' },
    { key: 'reports', label: '리포트·컨센서스' }
  ];
</script>

<div class="tab-bar" role="tablist">
  {#each tabs as t (t.key)}
    <button
      class="tab"
      class:active={activeTab === t.key}
      role="tab"
      aria-selected={activeTab === t.key}
      onclick={() => onSelect(t.key)}
    >
      {t.label}
    </button>
  {/each}
</div>

<style>
  .tab-bar {
    width: 100%;
    max-width: 1320px;
    box-sizing: border-box;
    display: flex;
    align-items: stretch;
    gap: var(--space-6);
    padding: 0 var(--space-3);
    border-bottom: 1px solid var(--border-subtle);
  }

  .tab {
    appearance: none;
    background: none;
    border: none;
    cursor: pointer;
    padding: var(--space-3) 2px;
    font-size: 15px;
    font-weight: 500;
    color: var(--text-tertiary);
    position: relative;
    transition: color 0.15s ease;
    white-space: nowrap;
  }

  .tab:hover {
    color: var(--text-secondary);
  }

  .tab.active {
    color: var(--text-primary);
    font-weight: 600;
  }

  .tab.active::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: -1px;
    height: 2px;
    background: var(--text-primary);
    border-radius: 1px;
  }
</style>
