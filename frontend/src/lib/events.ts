import type { TimelineEvent } from './types';

export type Category = 'alert' | 'pattern' | 'disclosure' | 'earnings' | 'dividend';

export interface EventItem {
  time: number;
  category: Category;
  event_type: string;
  triggered_at: string;
  trigger_values: Record<string, string>;
}

function sortKey(e: EventItem): number {
  return Date.parse(e.triggered_at) || e.time * 1000;
}

export function mergeTimelineEvents(timeline: TimelineEvent[], extra: EventItem[]): EventItem[] {
  const real: EventItem[] = (timeline ?? []).map((e) => ({
    time: e.time,
    category: e.event_kind as Category,
    event_type: e.event_type,
    triggered_at: e.triggered_at,
    trigger_values: e.trigger_values ?? {}
  }));
  return [...real, ...extra].sort((a, b) => sortKey(b) - sortKey(a));
}

export function eventKey(e: EventItem, index: number): string {
  return e.time + '|' + e.event_type + '|' + index;
}
