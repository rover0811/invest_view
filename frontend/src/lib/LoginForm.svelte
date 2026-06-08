<script lang="ts">
  import { login, setAuthToken } from './api';

  let { onSuccess, onClose }: { onSuccess: () => void; onClose?: () => void } = $props();

  let nickname = $state('');
  let loading = $state(false);
  let error = $state<string | null>(null);
  let inputEl = $state<HTMLInputElement | undefined>();

  $effect(() => {
    inputEl?.focus();
  });

  async function handleSubmit() {
    const n = nickname.trim();
    if (!n || loading) return;
    loading = true;
    error = null;
    try {
      const { token } = await login(n);
      setAuthToken(token);
      onSuccess();
    } catch (e) {
      error = e instanceof Error ? e.message : '로그인에 실패했습니다.';
    } finally {
      loading = false;
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      onClose?.();
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={() => onClose?.()}>
  <div class="overlay panel" onclick={(e) => e.stopPropagation()}>
    <div class="header">
      <div class="title-wrap">
        <h3>로그인</h3>
        <p class="sub">닉네임만 입력하면 바로 시작할 수 있어요.</p>
      </div>
      {#if onClose}
        <button class="close-btn" type="button" onclick={() => onClose?.()} aria-label="닫기">✕</button>
      {/if}
    </div>

    <div class="body">
      <label class="field-label" for="login-nickname">닉네임</label>
      <input
        id="login-nickname"
        class="field-input"
        type="text"
        placeholder="닉네임을 입력하세요"
        bind:value={nickname}
        bind:this={inputEl}
        onkeydown={handleKeydown}
        disabled={loading}
        autocomplete="off"
        maxlength="64"
      />

      {#if error}
        <p class="error" role="alert">{error}</p>
      {/if}

      <button
        class="submit"
        type="button"
        onclick={handleSubmit}
        disabled={loading || nickname.trim() === ''}
      >
        {loading ? '로그인 중…' : '로그인'}
      </button>
    </div>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: var(--overlay-scrim);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    z-index: 200;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: var(--space-4);
  }

  .overlay {
    width: 100%;
    max-width: 380px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: var(--shadow-overlay);
  }

  .header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-6) var(--space-6) var(--space-4);
    border-bottom: 1px solid var(--border-subtle);
  }

  .title-wrap {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .header h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .sub {
    margin: 0;
    font-size: 13px;
    color: var(--text-tertiary);
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    padding: 0;
    transition: color var(--dur-hover) var(--ease-out);
  }

  .close-btn:hover {
    color: var(--text-primary);
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-6);
  }

  .field-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  .field-input {
    width: 100%;
    box-sizing: border-box;
    background: var(--surface-floor);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 11px 14px;
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 14px;
    outline: none;
    transition: border-color var(--dur-hover) var(--ease-out);
  }

  .field-input:focus {
    border-color: var(--brand);
  }

  .field-input::placeholder {
    color: var(--text-tertiary);
  }

  .field-input:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .error {
    margin: 0;
    font-size: 13px;
    color: var(--price-down, #d64545);
  }

  .submit {
    margin-top: var(--space-2);
    background: var(--brand);
    color: var(--text-on-brand);
    border: none;
    border-radius: var(--radius-md);
    padding: 11px 16px;
    font-family: var(--font-sans);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity var(--dur-hover) var(--ease-out);
  }

  .submit:hover:not(:disabled) {
    opacity: 0.9;
  }

  .submit:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
