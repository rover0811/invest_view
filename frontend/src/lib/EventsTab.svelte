<script lang="ts">
  import type { StockData } from './types';
  import { mergeTimelineEvents, eventKey, type Category, type EventItem } from './events';

  let { data }: { data: StockData } = $props();

  // chart-panel.html:669-678 EVENT_LABELS + 더미 라벨
  const EVENT_LABELS: Record<string, string> = {
    GOLDEN_CROSS: '골든크로스',
    DEAD_CROSS: '데드크로스',
    RSI_OVERBOUGHT: 'RSI 과매수',
    RSI_OVERSOLD: 'RSI 과매도',
    MACD_BULLISH: 'MACD 매수',
    MACD_BEARISH: 'MACD 매도',
    PRICE_ALERT: '가격 알림',
    VI_IMMINENT: 'VI 임박',
    DISCLOSURE_QUARTERLY: '분기보고서 제출',
    DISCLOSURE_BUYBACK: '자기주식 취득 신고',
    EARNINGS_RELEASE: '1분기 실적발표',
    EX_DIVIDEND: '배당락'
  };

  const CATEGORY_META: Record<Category, { label: string; color: string }> = {
    alert: { label: '알림', color: 'var(--color-positive)' },
    pattern: { label: '패턴', color: 'var(--brand)' },
    disclosure: { label: '공시', color: 'var(--ma20)' },
    earnings: { label: '실적', color: 'var(--ma5)' },
    dividend: { label: '배당', color: 'var(--ma60)' }
  };

  // 컴포넌트 로컬 더미: 공시/실적/배당락 (mock에 없는 cold-path 이벤트)
  const DUMMY_EVENTS: EventItem[] = [
    {
      time: Math.floor(Date.parse('2026-05-30T16:00:00+09:00') / 1000),
      category: 'disclosure',
      event_type: 'DISCLOSURE_QUARTERLY',
      triggered_at: '2026-05-30T16:00:00+09:00',
      trigger_values: { 보고서: '분기보고서', 접수처: '금융감독원' }
    },
    {
      time: Math.floor(Date.parse('2026-05-20T09:00:00+09:00') / 1000),
      category: 'dividend',
      event_type: 'EX_DIVIDEND',
      triggered_at: '2026-05-20T09:00:00+09:00',
      trigger_values: { 배당금: '361원', 시가배당률: '0.50%' }
    },
    {
      time: Math.floor(Date.parse('2026-05-15T16:30:00+09:00') / 1000),
      category: 'earnings',
      event_type: 'EARNINGS_RELEASE',
      triggered_at: '2026-05-15T16:30:00+09:00',
      trigger_values: { 매출액: '79.1조', 영업이익: '6.6조', 전년대비: '+12%' }
    },
    {
      time: Math.floor(Date.parse('2026-05-08T17:00:00+09:00') / 1000),
      category: 'disclosure',
      event_type: 'DISCLOSURE_BUYBACK',
      triggered_at: '2026-05-08T17:00:00+09:00',
      trigger_values: { 취득금액: '3000억', 방식: '장내매수' }
    }
  ];

  let events = $derived(mergeTimelineEvents(data?.timeline ?? [], DUMMY_EVENTS));

  function labelOf(type: string): string {
    return EVENT_LABELS[type] ?? type;
  }

  function fmtDateTime(iso: string): { date: string; time: string } {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { date: iso, time: '' };
    const p = (n: number) => String(n).padStart(2, '0');
    return {
      date: `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())}`,
      time: `${p(d.getHours())}:${p(d.getMinutes())}`
    };
  }
</script>

<div class="events-tab">
  <div class="et-head">
    <h3 class="et-title">이벤트 타임라인</h3>
    <span class="et-sub">{data?._meta?.stock_name ?? ''} · 공시 · 실적 · 배당 · 시그널</span>
  </div>

  <ol class="timeline" style="--rail: var(--border-strong)">
    {#each events as ev, i (eventKey(ev, i))}
      {@const meta = CATEGORY_META[ev.category]}
      {@const dt = fmtDateTime(ev.triggered_at)}
      <li class="tl-item">
        <span class="tl-node" style="--c: {meta.color}">
          {#if ev.category === 'disclosure'}
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <path d="M4 2h5l3 3v9H4z" />
              <path d="M9 2v3h3" />
              <path d="M6 8h4M6 11h4" stroke-linecap="round" />
            </svg>
          {:else if ev.category === 'earnings'}
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <path d="M3 13h10" stroke-linecap="round" />
              <path d="M5 13V8M8 13V4M11 13v-3" stroke-linecap="round" />
            </svg>
          {:else if ev.category === 'dividend'}
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <circle cx="8" cy="8" r="5.5" />
              <path d="M6 9.5h2.2a1.2 1.2 0 0 0 0-2.4H6.8a1.2 1.2 0 0 1 0-2.4H9M8 4v8" stroke-linecap="round" />
            </svg>
          {:else if ev.category === 'alert'}
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <path d="M8 2l6 11H2z" stroke-linejoin="round" />
              <path d="M8 6.5v3M8 11.2v.2" stroke-linecap="round" />
            </svg>
          {:else}
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <path d="M2 11l3.5-4 2.5 2.5L13 4" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M10 4h3v3" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          {/if}
        </span>

        <div class="tl-card">
          <div class="tl-row">
            <span class="tl-badge" style="--c: {meta.color}">{meta.label}</span>
            <span class="tl-label">{labelOf(ev.event_type)}</span>
            <time class="tl-time tnum">{dt.date} <span class="tl-hm">{dt.time}</span></time>
          </div>
          {#if Object.keys(ev.trigger_values).length > 0}
            <div class="tl-values">
              {#each Object.entries(ev.trigger_values) as [k, v]}
                <span class="tl-chip">
                  <span class="tl-chip-k">{k}</span>
                  <span class="tl-chip-v tnum">{v}</span>
                </span>
              {/each}
            </div>
          {/if}
        </div>
      </li>
    {/each}
  </ol>
</div>

<style>
  .events-tab {
    width: 100%;
    box-sizing: border-box;
    padding: var(--space-4);
    background: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
  }

  .et-head {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
    margin-bottom: var(--space-6);
  }
  .et-title {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.01em;
  }
  .et-sub {
    font-size: 12px;
    color: var(--text-tertiary);
  }

  .timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    position: relative;
  }
  .timeline::before {
    content: '';
    position: absolute;
    top: 6px;
    bottom: 6px;
    left: 11px;
    width: 1px;
    background: var(--rail);
  }

  .tl-item {
    position: relative;
    display: flex;
    gap: var(--space-3);
    padding-bottom: var(--space-6);
  }
  .tl-item:last-child {
    padding-bottom: 0;
  }

  .tl-node {
    flex: 0 0 24px;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--c);
    background: var(--surface-overlay);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent);
    z-index: 1;
  }
  .tl-node svg {
    width: 14px;
    height: 14px;
  }

  .tl-card {
    flex: 1;
    min-width: 0;
    background: var(--surface-overlay);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: var(--space-3);
  }

  .tl-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .tl-badge {
    flex: 0 0 auto;
    font-size: 11px;
    font-weight: 600;
    color: var(--c);
    background: color-mix(in srgb, var(--c) 14%, transparent);
    border-radius: 999px;
    padding: 2px 8px;
  }
  .tl-label {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tl-time {
    flex: 0 0 auto;
    font-size: 11px;
    color: var(--text-tertiary);
  }
  .tl-hm {
    color: var(--text-secondary);
  }

  .tl-values {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    margin-top: var(--space-3);
  }
  .tl-chip {
    display: inline-flex;
    align-items: baseline;
    gap: var(--space-1);
    font-size: 11px;
    background: var(--surface-floor);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 3px 8px;
  }
  .tl-chip-k {
    color: var(--text-tertiary);
  }
  .tl-chip-v {
    color: var(--text-secondary);
    font-weight: 600;
  }
</style>
