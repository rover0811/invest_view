import { describe, it, expect } from 'vitest';
import { eventKey, mergeTimelineEvents, type EventItem } from './events';
import type { TimelineEvent } from './types';

function ev(time: number, type: string, kind: 'alert' | 'pattern' = 'pattern'): TimelineEvent {
  return {
    time,
    event_kind: kind,
    event_type: type,
    triggered_at: new Date(time * 1000).toISOString(),
    trigger_values: {}
  };
}

describe('eventKey', () => {
  it('produces unique keys for events sharing the same time and type', () => {
    const a: EventItem = { time: 1780293726, category: 'alert', event_type: 'VI_IMMINENT', triggered_at: 'x', trigger_values: {} };
    const b: EventItem = { ...a };
    expect(eventKey(a, 0)).not.toBe(eventKey(b, 1));
  });
});

describe('mergeTimelineEvents', () => {
  it('keeps every timeline row even when time and type collide (no dedupe)', () => {
    const timeline = [ev(1780293726, 'VI_IMMINENT', 'alert'), ev(1780293726, 'VI_IMMINENT', 'alert')];
    const merged = mergeTimelineEvents(timeline, []);
    const viCount = merged.filter((e) => e.event_type === 'VI_IMMINENT').length;
    expect(viCount).toBe(2);
  });

  it('produces a unique key for every merged event', () => {
    const timeline = [ev(1780293726, 'VI_IMMINENT', 'alert'), ev(1780293726, 'VI_IMMINENT', 'alert')];
    const merged = mergeTimelineEvents(timeline, []);
    const keys = merged.map((e, i) => eventKey(e, i));
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('sorts newest first by triggered_at', () => {
    const older = ev(Math.floor(Date.parse('2026-05-01T00:00:00+09:00') / 1000), 'GOLDEN_CROSS');
    const newer = ev(Math.floor(Date.parse('2026-05-10T00:00:00+09:00') / 1000), 'DEAD_CROSS');
    const merged = mergeTimelineEvents([older, newer], []);
    expect(merged[0].event_type).toBe('DEAD_CROSS');
  });

  it('maps event_kind to category', () => {
    const merged = mergeTimelineEvents([ev(1, 'PRICE_ALERT', 'alert')], []);
    expect(merged[0].category).toBe('alert');
  });

  it('merges extra (cold-path) events alongside timeline', () => {
    const extra: EventItem[] = [
      { time: 2, category: 'dividend', event_type: 'EX_DIVIDEND', triggered_at: '2026-05-20T09:00:00+09:00', trigger_values: {} }
    ];
    const merged = mergeTimelineEvents([ev(1, 'PRICE_ALERT', 'alert')], extra);
    expect(merged.some((e) => e.category === 'dividend')).toBe(true);
  });
});
