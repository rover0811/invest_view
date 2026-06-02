<script lang="ts">
  import type { StockData } from './types';

  let { data }: { data: StockData } = $props();

  interface Message {
    role: 'user' | 'bot';
    html: string;
    thinking?: string | null;
  }

  let messages = $state<Message[]>([]);
  let inputValue = $state('');
  let messagesContainer = $state<HTMLElement | undefined>();

  const STORAGE_KEY = 'aiPanelCollapsed';

  function initCollapsed(): boolean {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(STORAGE_KEY) === '1';
  }

  let collapsed = $state(initCollapsed());

  function persistCollapsed() {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    }
  }

  function toggle() {
    collapsed = !collapsed;
    persistCollapsed();
  }

  // Initialize welcome message
  $effect(() => {
    if (data && messages.length === 0) {
      messages.push({
        role: 'bot',
        html: `안녕하세요 👋 ${data._meta.stock_name}에 대해 궁금한 점을 물어보세요. 실시간 시세·수급·재무 데이터를 바탕으로 답변해 드려요.`
      });
    }
  });

  $effect(() => {
    if (messages.length > 0 && messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  });

  function answer(q: string): { thinking: string | null; html: string } {
    const t = q.toLowerCase();
    const s = data.snapshot || {};
    const td = data.tickDetail || {};
    const ind = data.indicators || {};
    const cons = data.consensus || [];
    const name = data._meta.stock_name;

    if (t.includes('올라') || t.includes('왜') || t.includes('상승')) {
      const rate = ((s.change_rate ?? 0) * 100).toFixed(2);
      return {
        thinking: '체결강도·수급 데이터 조회',
        html: `${name}는 현재 <b class="price-up">+${rate}%</b> 상승 중입니다. 체결강도가 <b>${(td.trade_strength ?? 0).toFixed(0)}%</b>로 매수세가 우위(매수 비중 ${((td.buy_ratio ?? 0) * 100).toFixed(0)}%)이고, 순매수 <b class="price-up">+${(td.net_buy_count ?? 0).toLocaleString('ko-KR')}</b>가 유입되고 있어요.<div class="ai-minichart"></div><div style="font-size:11px;color:var(--text-tertiary);margin-top:4px">↑ 최근 체결강도 추이</div>`
      };
    }
    if (t.includes('체결') || t.includes('수급') || t.includes('매수')) {
      return {
        thinking: 'tick 데이터 분석',
        html: `체결강도는 <b>${(td.trade_strength ?? 0).toFixed(1)}%</b>로 100을 넘어 매수 우위 상태예요. 매수 ${(td.buy_count ?? 0).toLocaleString('ko-KR')} vs 매도 ${(td.sell_count ?? 0).toLocaleString('ko-KR')}건으로 순매수가 이어지고 있습니다.`
      };
    }
    if (t.includes('목표') || t.includes('컨센') || t.includes('전망')) {
      const avg = cons.length ? Math.round(cons.reduce((a, c) => a + c.target_price, 0) / cons.length) : 0;
      const up = s.last_price ? (((avg - s.last_price) / s.last_price) * 100).toFixed(1) : '0';
      return {
        thinking: `증권사 리포트 ${cons.length}건 집계`,
        html: `증권사 평균 목표주가는 <b>${avg.toLocaleString('ko-KR')}원</b>으로 현재가 대비 <b class="price-up">+${up}%</b> 상승 여력이 있어요. 매수 의견이 ${cons.filter(c => c.investment_opinion === 'Buy').length}곳으로 우세합니다.`
      };
    }
    if (t.includes('per') || t.includes('지표') || t.includes('밸류') || t.includes('싸')) {
      return {
        thinking: '밸류에이션 지표 조회',
        html: `PER <b>${ind.per}배</b>, PBR <b>${ind.pbr}배</b>, ROE <b>${ind.roe}%</b> 수준이에요. 업종 평균 대비 밸류에이션 부담은 크지 않은 편입니다.`
      };
    }
    return {
      thinking: null,
      html: '죄송해요, 아직 목업 단계라 그 질문은 준비된 답변이 없어요. "왜 올라?", "체결강도 어때?", "목표주가는?" 같은 질문을 해보세요.'
    };
  }

  function ask(q: string) {
    if (!q.trim()) return;
    messages.push({ role: 'user', html: q });
    inputValue = '';
    
    const a = answer(q);
    setTimeout(() => {
      messages.push({ role: 'bot', html: a.html, thinking: a.thinking });
    }, 350);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      ask(inputValue);
    }
  }
</script>

<aside class="ai-panel" class:collapsed>
  {#if collapsed}
    <button
      class="ai-rail"
      type="button"
      onclick={toggle}
      aria-label="패널 펴기"
      aria-expanded="false"
    >
      <span class="ai-rail-chev" aria-hidden="true">«</span>
      <!-- Gemini brand gradient (logo) -->
      <svg class="ai-title-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="geminiGradRail" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#4285F4" />
            <stop offset="50%" stop-color="#9B72CB" />
            <stop offset="100%" stop-color="#D96570" />
          </linearGradient>
        </defs>
        <path fill="url(#geminiGradRail)" d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81" />
      </svg>
      <span class="ai-rail-label">AI 애널리스트</span>
    </button>
  {:else}
  <div class="ai-panel-head">
    <div class="ai-panel-titlerow">
      <button
        class="ai-collapse-btn"
        type="button"
        onclick={toggle}
        aria-label="패널 접기"
        aria-expanded="true"
      >
        <span aria-hidden="true">»</span>
      </button>
      <span class="ai-panel-title">
        <svg class="ai-title-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <defs>
            <linearGradient id="geminiGradHead" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#4285F4" />
              <stop offset="50%" stop-color="#9B72CB" />
              <stop offset="100%" stop-color="#D96570" />
            </linearGradient>
          </defs>
          <path fill="url(#geminiGradHead)" d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81" />
        </svg>
        AI 애널리스트
      </span>
    </div>
    <span class="ai-panel-sub">종목에 대해 무엇이든 물어보세요</span>
  </div>
  
  <div class="ai-messages" bind:this={messagesContainer}>
    {#each messages as msg}
      <div class="ai-msg {msg.role}">
        {#if msg.thinking}
          <div class="ai-thinking">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="M20 20l-4-4" />
            </svg>
            {msg.thinking}
          </div>
        {/if}
        <div class="ai-bubble">{@html msg.html}</div>
      </div>
    {/each}
  </div>
  
  <div class="ai-suggestions">
    <button class="ai-sug" type="button" onclick={() => ask('왜 오르고 있어?')}>왜 오르고 있어?</button>
    <button class="ai-sug" type="button" onclick={() => ask('체결강도 어때?')}>체결강도 어때?</button>
    <button class="ai-sug" type="button" onclick={() => ask('목표주가는?')}>목표주가는?</button>
  </div>
  
  <div class="ai-input-row">
    <input 
      class="ai-input" 
      type="text" 
      placeholder="{data._meta.stock_name}에 대해 물어보기" 
      bind:value={inputValue}
      onkeydown={handleKeydown}
    />
    <button class="ai-send" type="button" onclick={() => ask(inputValue)}>전송</button>
  </div>
  {/if}
</aside>

<style>
  .ai-panel {
    position: relative;
    flex-grow: 0;
    flex-shrink: 0;
    flex-basis: var(--ai-expanded, 500px);
    display: flex;
    flex-direction: column;
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    overflow: hidden;
    height: 100%;
    transition: flex-basis 0.2s var(--ease-out);
  }
  .ai-panel.collapsed {
    flex-basis: 48px;
  }

  .ai-rail {
    flex: 1;
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) 0;
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--text-secondary);
    transition: background var(--dur-hover) var(--ease-out), color var(--dur-hover) var(--ease-out);
  }
  .ai-rail:hover { background: var(--surface-overlay); color: var(--text-primary); }
  .ai-rail-chev {
    font-size: 18px;
    font-weight: 700;
    line-height: 1;
    color: var(--text-tertiary);
  }
  .ai-rail:hover .ai-rail-chev { color: var(--brand); }
  .ai-rail-label {
    writing-mode: vertical-rl;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--text-primary);
  }

  .ai-panel-head {
    padding: var(--space-4);
    border-bottom: 1px solid var(--border-subtle);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ai-panel-titlerow {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: var(--space-2);
  }
  .ai-panel-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .ai-collapse-btn {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    font-size: 16px;
    font-weight: 700;
    line-height: 1;
    color: var(--text-tertiary);
    background: var(--surface-overlay);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: color var(--dur-hover) var(--ease-out), background var(--dur-hover) var(--ease-out), border-color var(--dur-hover) var(--ease-out);
  }
  .ai-collapse-btn:hover {
    color: var(--brand);
    background: var(--surface-raised);
    border-color: var(--border-strong);
  }
  .ai-title-icon { flex-shrink: 0; }
  .ai-panel-sub { font-size: 12px; color: var(--text-tertiary); }

  .ai-messages {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .ai-msg { display: flex; flex-direction: column; max-width: 86%; }
  .ai-msg.user { align-self: flex-end; align-items: flex-end; }
  .ai-msg.bot { align-self: flex-start; align-items: flex-start; }

  .ai-bubble {
    font-size: 13px;
    line-height: 1.55;
    padding: 9px 12px;
    border-radius: var(--radius-md);
  }
  .ai-msg.user .ai-bubble { background: var(--brand); color: var(--text-on-brand); border-bottom-right-radius: 4px; }
  .ai-msg.bot .ai-bubble { background: var(--surface-overlay); color: var(--text-primary); border-bottom-left-radius: 4px; }

  .ai-thinking {
    font-size: 11px;
    color: var(--text-tertiary);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 5px;
  }

  :global(.ai-minichart) {
    margin-top: var(--space-2);
    width: 100%;
    height: 90px;
    border-radius: var(--radius-sm);
    background: var(--surface-floor);
    border: 1px solid var(--border-subtle);
  }

  .ai-suggestions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    padding: 0 var(--space-4) var(--space-3);
  }
  .ai-sug {
    font-size: 12px;
    color: var(--text-secondary);
    background: var(--surface-overlay);
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    padding: 5px 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .ai-sug:hover { color: var(--text-primary); background: var(--surface-raised); }

  .ai-input-row {
    display: flex;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4) var(--space-4);
    border-top: 1px solid var(--border-subtle);
  }
  .ai-input {
    flex: 1;
    min-width: 0;
    background: var(--surface-floor);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 9px 12px;
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 13px;
    outline: none;
  }
  .ai-input:focus { border-color: var(--brand); }
  .ai-input::placeholder { color: var(--text-tertiary); }
  .ai-send {
    background: var(--brand);
    color: var(--text-on-brand);
    border: none;
    border-radius: var(--radius-md);
    padding: 0 16px;
    font-family: var(--font-sans);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
</style>
