<script lang="ts">
  import type { StockData } from './types';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import {
    createAgentSession,
    streamAgentChat,
    regenerateAgentChat,
    hasAuthToken,
    type AgentStreamCallbacks,
  } from './api';

  let { data }: { data: StockData } = $props();

  // Render agent markdown (bold/headings/lists) as sanitized HTML.
  // Bot messages ONLY — user input and error strings are rendered as plain text.
  function renderMarkdown(src: string): string {
    return DOMPurify.sanitize(marked.parse(src, { async: false, breaks: true }) as string);
  }

  interface Message {
    role: 'user' | 'bot';
    text: string;
    streaming?: boolean;
    error?: boolean;
    messageId?: string;
  }

  let messages = $state<Message[]>([]);
  let inputValue = $state('');
  let messagesContainer = $state<HTMLElement | undefined>();
  let sessionId = $state<string | null>(null);
  let streaming = $state(false);
  let activeSymbol = $state<string | null>(null);
  let abortController: AbortController | null = null;
  // Non-reactive synchronous lock to close the batched-$state race where a
  // double Enter (or Enter + button) fires ask() twice before `streaming` flips.
  let _sending = false;

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

  // A session is bound to one ticker; switching symbols resets the conversation.
  $effect(() => {
    const sym = data?._meta.symbol;
    if (sym && sym !== activeSymbol) {
      activeSymbol = sym;
      sessionId = null;
      abortController?.abort();
      abortController = null;
      streaming = false;
      messages = [
        {
          role: 'bot',
          text: `안녕하세요 👋 ${data._meta.stock_name}에 대해 궁금한 점을 물어보세요. 실시간 시세·수급·재무 데이터를 바탕으로 답변해 드려요.`,
        },
      ];
    }
  });

  $effect(() => {
    messages.length;
    messages[messages.length - 1]?.text;
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  });

  async function ensureSession(): Promise<string> {
    if (sessionId) return sessionId;
    const s = await createAgentSession(data._meta.symbol);
    sessionId = s.session_id;
    return s.session_id;
  }

  function runStream(
    invoke: (cbs: AgentStreamCallbacks, signal: AbortSignal) => Promise<void>,
    botIndex: number,
  ) {
    abortController = new AbortController();
    streaming = true;
    const cbs: AgentStreamCallbacks = {
      onToken: (t) => {
        messages[botIndex].text += t;
      },
      onDone: (info) => {
        messages[botIndex].streaming = false;
        messages[botIndex].messageId = info.message_id;
      },
      onError: (m) => {
        messages[botIndex].streaming = false;
        messages[botIndex].error = true;
        messages[botIndex].text = messages[botIndex].text
          ? `${messages[botIndex].text}\n\n[오류] ${m}`
          : m;
      },
    };
    invoke(cbs, abortController.signal).finally(() => {
      if (messages[botIndex]?.streaming) messages[botIndex].streaming = false;
      streaming = false;
      abortController = null;
      _sending = false;
    });
  }

  async function ask(q: string) {
    const text = q.trim();
    if (!text || streaming || _sending) return;
    _sending = true;
    inputValue = '';

    if (!hasAuthToken()) {
      messages.push({ role: 'user', text });
      messages.push({
        role: 'bot',
        text: '로그인이 필요합니다. (개발 환경에서는 VITE_DEV_JWT 환경변수를 설정하세요.)',
        error: true,
      });
      _sending = false;
      return;
    }

    messages.push({ role: 'user', text });

    let sid: string;
    try {
      sid = await ensureSession();
    } catch (e) {
      messages.push({
        role: 'bot',
        text: e instanceof Error ? e.message : '세션을 생성하지 못했습니다.',
        error: true,
      });
      _sending = false;
      return;
    }

    messages.push({ role: 'bot', text: '', streaming: true });
    const botIndex = messages.length - 1;
    runStream((cbs, signal) => streamAgentChat(sid, text, cbs, { signal }), botIndex);
  }

  function regenerate() {
    if (streaming || !sessionId) return;
    let mid: string | undefined;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'bot' && messages[i].messageId) {
        mid = messages[i].messageId;
        break;
      }
    }
    if (!mid) return;
    const sid = sessionId;
    messages.push({ role: 'bot', text: '', streaming: true });
    const botIndex = messages.length - 1;
    runStream((cbs, signal) => regenerateAgentChat(sid, mid!, cbs, { signal }), botIndex);
  }

  function stop() {
    abortController?.abort();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !streaming) {
      e.preventDefault();
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
    {#each messages as msg, i}
      <div class="ai-msg {msg.role}">
        <div
          class="ai-bubble"
          class:error={msg.error}
          class:streaming={msg.streaming}
          class:markdown={msg.role === 'bot' && !msg.error}
        >{#if msg.role === 'bot' && !msg.error}{@html renderMarkdown(msg.text)}{:else}{msg.text}{/if}{#if msg.streaming}<span class="ai-caret" aria-hidden="true"></span>{/if}</div>
        {#if msg.role === 'bot' && msg.messageId && !streaming && i === messages.length - 1}
          <button class="ai-regen" type="button" onclick={regenerate}>다시 생성</button>
        {/if}
      </div>
    {/each}
  </div>
  
  <div class="ai-suggestions">
    <button class="ai-sug" type="button" disabled={streaming} onclick={() => ask('왜 오르고 있어?')}>왜 오르고 있어?</button>
    <button class="ai-sug" type="button" disabled={streaming} onclick={() => ask('체결강도 어때?')}>체결강도 어때?</button>
    <button class="ai-sug" type="button" disabled={streaming} onclick={() => ask('목표주가는?')}>목표주가는?</button>
  </div>
  
  <div class="ai-input-row">
    <input 
      class="ai-input" 
      type="text" 
      placeholder="{data._meta.stock_name}에 대해 물어보기" 
      bind:value={inputValue}
      onkeydown={handleKeydown}
    />
    {#if streaming}
      <button class="ai-send ai-stop" type="button" onclick={stop}>중지</button>
    {:else}
      <button class="ai-send" type="button" onclick={() => ask(inputValue)}>전송</button>
    {/if}
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
    white-space: pre-wrap;
    word-break: break-word;
  }
  /* Rendered markdown manages its own block spacing; pre-wrap would add giant gaps. */
  .ai-bubble.markdown { white-space: normal; }
  .ai-msg.user .ai-bubble { background: var(--brand); color: var(--text-on-brand); border-bottom-right-radius: 4px; }
  .ai-msg.bot .ai-bubble { background: var(--surface-overlay); color: var(--text-primary); border-bottom-left-radius: 4px; }
  .ai-bubble.error { color: var(--price-down, #d64545); }

  /* Compact markdown styling for the chat sidebar bubble. */
  .ai-bubble.markdown :global(> *:first-child) { margin-top: 0; }
  .ai-bubble.markdown :global(> *:last-child) { margin-bottom: 0; }
  .ai-bubble.markdown :global(p) { margin: 0 0 8px; }
  .ai-bubble.markdown :global(h1),
  .ai-bubble.markdown :global(h2),
  .ai-bubble.markdown :global(h3),
  .ai-bubble.markdown :global(h4) {
    margin: 10px 0 6px;
    font-weight: 700;
    line-height: 1.3;
  }
  .ai-bubble.markdown :global(h1) { font-size: 16px; }
  .ai-bubble.markdown :global(h2) { font-size: 15px; }
  .ai-bubble.markdown :global(h3),
  .ai-bubble.markdown :global(h4) { font-size: 14px; }
  .ai-bubble.markdown :global(ul),
  .ai-bubble.markdown :global(ol) { margin: 0 0 8px; padding-left: 20px; }
  .ai-bubble.markdown :global(li) { margin: 2px 0; }
  .ai-bubble.markdown :global(li > p) { margin: 0; }
  .ai-bubble.markdown :global(strong) { font-weight: 700; }
  .ai-bubble.markdown :global(em) { font-style: italic; }
  .ai-bubble.markdown :global(a) { color: var(--brand); text-decoration: underline; }
  .ai-bubble.markdown :global(code) {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.92em;
    background: var(--surface-floor);
    padding: 1px 4px;
    border-radius: var(--radius-sm, 4px);
  }
  .ai-bubble.markdown :global(pre) {
    margin: 0 0 8px;
    padding: 8px 10px;
    background: var(--surface-floor);
    border-radius: var(--radius-sm, 4px);
    overflow-x: auto;
  }
  .ai-bubble.markdown :global(pre code) { background: none; padding: 0; }
  .ai-bubble.markdown :global(blockquote) {
    margin: 0 0 8px;
    padding-left: 10px;
    border-left: 2px solid var(--border-strong, var(--border-subtle));
    color: var(--text-secondary);
  }
  .ai-bubble.streaming:empty::before {
    content: '생각 중';
    color: var(--text-tertiary);
  }

  .ai-caret {
    display: inline-block;
    width: 7px;
    height: 1em;
    margin-left: 2px;
    vertical-align: text-bottom;
    background: currentColor;
    opacity: 0.7;
    animation: ai-blink 1s step-start infinite;
  }
  @keyframes ai-blink {
    50% { opacity: 0; }
  }

  .ai-regen {
    margin-top: 6px;
    font-size: 11px;
    color: var(--text-secondary);
    background: transparent;
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    padding: 3px 10px;
    cursor: pointer;
    transition: color var(--dur-hover) var(--ease-out), background var(--dur-hover) var(--ease-out);
  }
  .ai-regen:hover { color: var(--text-primary); background: var(--surface-overlay); }

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
  .ai-sug:disabled { opacity: 0.5; cursor: not-allowed; }

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
  .ai-stop {
    background: var(--surface-raised);
    color: var(--text-primary);
    border: 1px solid var(--border-strong);
  }
</style>
