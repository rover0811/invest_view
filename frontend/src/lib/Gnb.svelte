<script lang="ts">
  import SearchOverlay from './SearchOverlay.svelte';
  import { appState } from './stores.svelte';

  let { onNavigate }: { onNavigate: (path: string) => void } = $props();

  let searchOpen = $state(false);

  function handleKeydown(e: KeyboardEvent) {
    // Ignore if typing in an input
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      return;
    }
    if (e.key === '/') {
      e.preventDefault();
      searchOpen = true;
    }
  }

  $effect(() => {
    window.addEventListener('keydown', handleKeydown);
    return () => window.removeEventListener('keydown', handleKeydown);
  });
</script>

<header class="gnb">
  <div class="gnb-inner">
    <div class="left">
      <button class="logo" onclick={() => onNavigate('/')}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 2L2 7L12 12L22 7L12 2Z" fill="var(--brand)"/>
          <path d="M2 17L12 22L22 17M2 12L12 17L22 12" stroke="var(--brand)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>investview</span>
      </button>
      <nav class="menu">
        <button class="menu-item {appState.route.view === 'home' ? 'active' : ''}" onclick={() => onNavigate('/')}>홈</button>
        <button class="menu-item">피드</button>
        <button class="menu-item {appState.route.view === 'stock' ? 'active' : ''}">주식 골라보기</button>
      </nav>
    </div>

    <div class="right">
      <button class="search-pill" onclick={() => searchOpen = true}>
        <span class="icon">🔍</span>
        <span class="placeholder">종목 검색</span>
        <span class="shortcut">/</span>
      </button>
      <div class="profile">
        <div class="avatar"></div>
      </div>
    </div>
  </div>
</header>

<SearchOverlay 
  open={searchOpen} 
  onClose={() => searchOpen = false} 
  onNavigate={onNavigate} 
/>

<style>
  .gnb {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 60px;
    background: var(--surface-floor);
    border-bottom: 1px solid var(--border-subtle);
    z-index: 50;
    display: flex;
    justify-content: center;
  }

  .gnb-inner {
    width: 100%;
    max-width: 1320px;
    height: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 var(--space-6);
  }

  .left {
    display: flex;
    align-items: center;
    gap: var(--space-8);
  }

  .logo {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    color: var(--text-primary);
    font-size: 18px;
    font-weight: 700;
  }

  .menu {
    display: flex;
    gap: var(--space-6);
  }

  .menu-item {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
    color: var(--text-tertiary);
    transition: color 0.2s;
  }

  .menu-item:hover {
    color: var(--text-secondary);
  }

  .menu-item.active {
    color: var(--text-primary);
  }

  .right {
    display: flex;
    align-items: center;
    gap: var(--space-4);
  }

  .search-pill {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: 20px;
    padding: 6px 12px 6px 16px;
    cursor: pointer;
    transition: background 0.2s, border-color 0.2s;
  }

  .search-pill:hover {
    background: var(--surface-raised);
    border-color: var(--border-strong);
  }

  .icon {
    font-size: 14px;
  }

  .placeholder {
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 500;
  }

  .shortcut {
    background: var(--surface-floor);
    color: var(--text-tertiary);
    font-size: 12px;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid var(--border-subtle);
    margin-left: var(--space-2);
    font-family: var(--font-mono);
  }

  .profile {
    display: flex;
    align-items: center;
  }

  .avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
  }
</style>