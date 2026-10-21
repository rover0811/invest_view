export type PriceState = 'POSITIVE' | 'NEUTRAL' | 'NEGATIVE';
export type DocumentDirection = -1 | 0 | 1 | null;
export type DocumentScope = 'SYMBOL' | 'SECTOR' | 'MARKET' | null;
export type DocumentFailReason = 'LISTING_ONLY' | 'AD' | 'NO_EVIDENCE' | null;
export type SignalStatus = 'SHOWN' | 'HIDDEN' | 'NO_CANDIDATE';
export type ReplayLogKind = 'document' | 'judgement' | 'state' | 'request' | 'llm' | 'fallback' | 'signal';
export type ReplayBarInterval = 1 | 3 | 5;

export interface ReplayBar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface ReplayStateChange {
  t: string;
  state: PriceState;
  move_id: string;
  move_version: number;
  return_pct: number;
}

export interface ReplayDocument {
  id: string;
  title: string;
  publisher: string;
  published_at: string;
  judged: boolean;
  direction: DocumentDirection;
  scope: DocumentScope;
  fail_reason: DocumentFailReason;
}

export interface ReplayCause {
  rank: number;
  label: string;
  explanation: string;
  scope: Exclude<DocumentScope, null>;
  source_document_ids: string[];
}

export interface ReplayDecomposition {
  global: number;
  market: number;
  sector: number;
  idio: number;
  dominant_layer: 'GLOBAL' | 'MARKET' | 'SECTOR' | 'IDIO';
}

export interface ReplaySignal {
  at: string;
  move_version: number;
  version: number;
  status: SignalStatus;
  headline_label: string;
  particle: '로' | '으로';
  decided_by: 'llm' | null;
  model: string | null;
  prompt_version: string;
  trigger: string;
  causes: ReplayCause[];
  decomposition: ReplayDecomposition | null;
}

export interface ReplayLogEntry {
  t: string;
  kind: ReplayLogKind;
  text: string;
}

export interface ReplayData {
  contract_version: 1;
  symbol: string;
  name: string;
  date: string;
  prev_close: number;
  thresholds: {
    enter_pct: number;
    exit_pct: number;
  };
  bars: ReplayBar[];
  states: ReplayStateChange[];
  documents: ReplayDocument[];
  signals: ReplaySignal[];
  log: ReplayLogEntry[];
}

export interface ReplayFrame {
  bar: ReplayBar | undefined;
  state: ReplayStateChange | undefined;
  signal: ReplaySignal | undefined;
  shownSignal: ReplaySignal | undefined;
  documents: ReplayDocument[];
  logs: ReplayLogEntry[];
  latestLog: ReplayLogEntry | undefined;
  returnPct: number;
}

export interface StateBand {
  start: string;
  end: string;
  state: PriceState;
}

export interface ReplayLoadResult {
  replay: ReplayData;
  fixture: boolean;
}

export interface ReplayTimeline {
  startMs: number;
  closeMs: number;
  endMs: number;
  sessionEndPosition: number;
}

export interface ReplayDensityBucket {
  position: number;
  count: number;
  intensity: number;
}

export interface ReplayMarkerCluster<T> {
  items: T[];
  position: number;
}

export interface ReplayPixelRange {
  start: number;
  end: number;
  row?: number;
}

export interface ReplayAnnotationTagLayout {
  x: number;
  row: number;
}

export type ReplaySeriesUpdatePlan =
  | { mode: 'none' }
  | { mode: 'replace'; bars: ReplayBar[] }
  | { mode: 'update'; bars: ReplayBar[] };

export type ReplayFetch = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isBoundedString(value: unknown, maxLength = 2_000): value is string {
  return typeof value === 'string' && value.length <= maxLength;
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function isReplayBar(value: unknown): value is ReplayBar {
  return isRecord(value)
    && isTimestamp(value.t)
    && isFiniteNumber(value.o)
    && isFiniteNumber(value.h)
    && isFiniteNumber(value.l)
    && isFiniteNumber(value.c)
    && isFiniteNumber(value.v)
    && value.v >= 0;
}

function isStateChange(value: unknown): value is ReplayStateChange {
  return isRecord(value)
    && isTimestamp(value.t)
    && (value.state === 'POSITIVE' || value.state === 'NEUTRAL' || value.state === 'NEGATIVE')
    && isBoundedString(value.move_id, 200)
    && Number.isInteger(value.move_version)
    && isFiniteNumber(value.return_pct);
}

function isDocument(value: unknown): value is ReplayDocument {
  return isRecord(value)
    && isBoundedString(value.id, 200)
    && isBoundedString(value.title, 500)
    && isBoundedString(value.publisher, 200)
    && isTimestamp(value.published_at)
    && typeof value.judged === 'boolean'
    && (value.direction === -1 || value.direction === 0 || value.direction === 1 || value.direction === null)
    && (value.scope === 'SYMBOL' || value.scope === 'SECTOR' || value.scope === 'MARKET' || value.scope === null)
    && (value.fail_reason === 'LISTING_ONLY' || value.fail_reason === 'AD' || value.fail_reason === 'NO_EVIDENCE' || value.fail_reason === null);
}

function isCause(value: unknown): value is ReplayCause {
  return isRecord(value)
    && Number.isInteger(value.rank)
    && isBoundedString(value.label, 500)
    && isBoundedString(value.explanation)
    && (value.scope === 'SYMBOL' || value.scope === 'SECTOR' || value.scope === 'MARKET')
    && Array.isArray(value.source_document_ids)
    && value.source_document_ids.length <= 100
    && value.source_document_ids.every((id) => isBoundedString(id, 200));
}

function isDecomposition(value: unknown): value is ReplayDecomposition | null {
  if (value === null) return true;
  return isRecord(value)
    && isFiniteNumber(value.global)
    && isFiniteNumber(value.market)
    && isFiniteNumber(value.sector)
    && isFiniteNumber(value.idio)
    && (value.dominant_layer === 'GLOBAL'
      || value.dominant_layer === 'MARKET'
      || value.dominant_layer === 'SECTOR'
      || value.dominant_layer === 'IDIO');
}

function isSignal(value: unknown): value is ReplaySignal {
  return isRecord(value)
    && isTimestamp(value.at)
    && Number.isInteger(value.move_version)
    && Number.isInteger(value.version)
    && (value.status === 'SHOWN' || value.status === 'HIDDEN' || value.status === 'NO_CANDIDATE')
    && isBoundedString(value.headline_label, 500)
    && (value.particle === '로' || value.particle === '으로')
    && (value.decided_by === 'llm' || value.decided_by === null)
    && (isBoundedString(value.model, 500) || value.model === null)
    && isBoundedString(value.prompt_version, 500)
    && isBoundedString(value.trigger, 500)
    && Array.isArray(value.causes)
    && value.causes.length <= 100
    && value.causes.every(isCause)
    && isDecomposition(value.decomposition);
}

function isLogEntry(value: unknown): value is ReplayLogEntry {
  return isRecord(value)
    && isTimestamp(value.t)
    && (value.kind === 'document'
      || value.kind === 'judgement'
      || value.kind === 'state'
      || value.kind === 'request'
      || value.kind === 'llm'
      || value.kind === 'fallback'
      || value.kind === 'signal')
    && isBoundedString(value.text);
}

function isAscending<T>(items: T[], getTime: (item: T) => string): boolean {
  return items.every((item, index) => index === 0 || Date.parse(getTime(items[index - 1])) <= Date.parse(getTime(item)));
}

export function isReplayData(value: unknown): value is ReplayData {
  if (!isRecord(value)
    || value.contract_version !== 1
    || typeof value.symbol !== 'string'
    || !/^\d{6}$/.test(value.symbol)
    || !isBoundedString(value.name, 100)
    || typeof value.date !== 'string'
    || !/^\d{4}-\d{2}-\d{2}$/.test(value.date)
    || !isFiniteNumber(value.prev_close)
    || value.prev_close <= 0
    || !isRecord(value.thresholds)
    || !isFiniteNumber(value.thresholds.enter_pct)
    || !isFiniteNumber(value.thresholds.exit_pct)
    || !Array.isArray(value.bars)
    || value.bars.length === 0
    || value.bars.length > 500
    || !value.bars.every(isReplayBar)
    || !Array.isArray(value.states)
    || value.states.length > 100
    || !value.states.every(isStateChange)
    || !Array.isArray(value.documents)
    || value.documents.length > 1_000
    || !value.documents.every(isDocument)
    || !Array.isArray(value.signals)
    || value.signals.length > 500
    || !value.signals.every(isSignal)
    || !Array.isArray(value.log)
    || value.log.length > 2_000
    || !value.log.every(isLogEntry)) {
    return false;
  }

  const data = value as unknown as ReplayData;
  const barSpan = Date.parse(data.bars[data.bars.length - 1].t) - Date.parse(data.bars[0].t);
  return barSpan >= 0
    && barSpan <= 24 * 60 * 60 * 1_000
    && data.bars.every((bar) => bar.t.startsWith(`${data.date}T`))
    && isAscending(data.bars, (bar) => bar.t)
    && isAscending(data.states, (state) => state.t)
    && isAscending(data.signals, (signal) => signal.at)
    && isAscending(data.log, (entry) => entry.t);
}

export async function loadReplayData(
  symbol: string,
  date: string,
  signal?: AbortSignal,
  request: ReplayFetch = fetch,
): Promise<ReplayLoadResult> {
  const requestedPath = `/replay/${encodeURIComponent(symbol)}-${encodeURIComponent(date)}.json`;
  const response = await request(requestedPath, { signal });
  const contentType = response.headers.get('content-type') ?? '';

  if (response.ok && contentType.includes('application/json')) {
    const replay: unknown = await response.json();
    if (!isReplayData(replay)) throw new Error('재생 데이터의 형식이 올바르지 않습니다.');
    if (replay.symbol !== symbol || replay.date !== date) {
      throw new Error('요청한 종목과 거래일이 재생 데이터와 일치하지 않습니다.');
    }
    return { replay, fixture: false };
  }

  const fallback = await request('/replay/demo-fixture.json', { signal });
  if (!fallback.ok) throw new Error('재생 데이터를 불러오지 못했습니다.');
  const replay: unknown = await fallback.json();
  if (!isReplayData(replay)) throw new Error('데모 데이터의 형식이 올바르지 않습니다.');
  return { replay, fixture: true };
}

function latestAtOrBefore<T>(items: T[], playhead: string, getTime: (item: T) => string): T | undefined {
  let latest: T | undefined;
  for (const item of items) {
    if (Date.parse(getTime(item)) <= Date.parse(playhead)) {
      latest = item;
    }
  }
  return latest;
}

export function selectReplayFrame(data: ReplayData, playhead: string): ReplayFrame {
  const bar = latestAtOrBefore(data.bars, playhead, (item) => item.t);
  const state = latestAtOrBefore(data.states, playhead, (item) => item.t);
  const signal = latestAtOrBefore(data.signals, playhead, (item) => item.at);
  const shownSignal = latestAtOrBefore(
    data.signals.filter((item) => item.status === 'SHOWN'),
    playhead,
    (item) => item.at,
  );
  const documents = data.documents.filter((item) => Date.parse(item.published_at) <= Date.parse(playhead));
  const logs = data.log.filter((item) => Date.parse(item.t) <= Date.parse(playhead));
  const returnPct = bar == null ? 0 : ((bar.c - data.prev_close) / data.prev_close) * 100;

  return {
    bar,
    state,
    signal,
    shownSignal,
    documents,
    logs,
    latestLog: logs.at(-1),
    returnPct,
  };
}

export function visibleBarsAt(bars: ReplayBar[], playhead: string): ReplayBar[] {
  const playheadTime = Date.parse(playhead);
  return bars.filter((bar) => Date.parse(bar.t) <= playheadTime);
}

export function aggregateReplayBars(bars: ReplayBar[], interval: ReplayBarInterval): ReplayBar[] {
  if (interval === 1) return bars;

  const intervalMs = interval * 60_000;
  const aggregated: ReplayBar[] = [];

  for (const bar of bars) {
    const bucketStartMs = Math.floor(Date.parse(bar.t) / intervalMs) * intervalMs;
    const current = aggregated.at(-1);

    if (current && Date.parse(current.t) === bucketStartMs) {
      current.h = Math.max(current.h, bar.h);
      current.l = Math.min(current.l, bar.l);
      current.c = bar.c;
      current.v += bar.v;
      continue;
    }

    const bucketStart = new Date(bucketStartMs + 9 * 60 * 60_000).toISOString().slice(0, 19) + '+09:00';
    aggregated.push({ t: bucketStart, o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v });
  }

  return aggregated;
}

export function replayTimeToChartCoordinate(
  bars: ReplayBar[],
  targetTime: string,
  plotWidth: number,
): number {
  if (bars.length === 0) return 0;
  const startMs = Date.parse(bars[0].t);
  const endMs = Date.parse(bars.at(-1)!.t);
  const targetMs = Date.parse(targetTime);
  const ratio = Math.max(0, Math.min(1, (targetMs - startMs) / Math.max(1, endMs - startMs)));
  return ratio * Math.max(0, plotWidth);
}

function sameReplayBar(left: ReplayBar, right: ReplayBar): boolean {
  return left.t === right.t
    && left.o === right.o
    && left.h === right.h
    && left.l === right.l
    && left.c === right.c
    && left.v === right.v;
}

export function planReplaySeriesUpdate(
  previous: ReplayBar[] | null,
  next: ReplayBar[],
  forceReplace = false,
): ReplaySeriesUpdatePlan {
  if (previous == null || forceReplace || next.length < previous.length) {
    return { mode: 'replace', bars: next };
  }

  let firstChanged = 0;
  const sharedLength = Math.min(previous.length, next.length);
  while (firstChanged < sharedLength && sameReplayBar(previous[firstChanged], next[firstChanged])) {
    firstChanged += 1;
  }

  if (firstChanged === next.length) return { mode: 'none' };

  const earliestIncrementalIndex = Math.max(0, previous.length - 1);
  if (firstChanged < earliestIncrementalIndex) {
    return { mode: 'replace', bars: next };
  }

  return { mode: 'update', bars: next.slice(firstChanged) };
}

export function assembleHeadline(signal: ReplaySignal | undefined, name: string, rate: number): string | null {
  if (signal?.status !== 'SHOWN') {
    return null;
  }

  const rounded = Number(Math.abs(rate).toFixed(1));
  if (rounded === 0) {
    return `${signal.headline_label}에도 ${name} 보합`;
  }

  const direction = rate > 0 ? '상승' : '하락';
  return `${signal.headline_label}${signal.particle} ${name} ${rounded.toFixed(1)}% ${direction}`;
}

export function computeStateBands(states: ReplayStateChange[], end: string): StateBand[] {
  const endTime = Date.parse(end);
  const visible = states
    .filter((state) => Date.parse(state.t) <= endTime)
    .toSorted((a, b) => Date.parse(a.t) - Date.parse(b.t));

  return visible.map((state, index) => ({
    start: state.t,
    end: visible[index + 1]?.t ?? end,
    state: state.state,
  }));
}

export function buildReplayEvents(data: ReplayData): string[] {
  const moments = new Set<string>();
  if (data.bars[0]) moments.add(data.bars[0].t);
  if (data.bars.at(-1)) moments.add(data.bars.at(-1)!.t);
  data.states.forEach((item) => moments.add(item.t));
  data.documents.forEach((item) => moments.add(item.published_at));
  data.signals.forEach((item) => moments.add(item.at));
  data.log.forEach((item) => moments.add(item.t));
  return [...moments].toSorted((a, b) => Date.parse(a) - Date.parse(b));
}

const SESSION_END_POSITION = 82;

export function buildReplayTimeline(data: ReplayData): ReplayTimeline {
  const startMs = Date.parse(data.bars[0].t);
  const closeMs = Date.parse(data.bars.at(-1)!.t);
  const eventTimes = [
    ...data.states.map((item) => Date.parse(item.t)),
    ...data.documents.map((item) => Date.parse(item.published_at)),
    ...data.signals.map((item) => Date.parse(item.at)),
    ...data.log.map((item) => Date.parse(item.t)),
  ].filter((time) => time >= startMs);
  const endMs = Math.max(closeMs, ...eventTimes);

  return {
    startMs,
    closeMs,
    endMs,
    sessionEndPosition: endMs > closeMs ? SESSION_END_POSITION : 100,
  };
}

export function replayTimeToPosition(timeline: ReplayTimeline, time: number): number {
  const clamped = Math.max(timeline.startMs, Math.min(timeline.endMs, time));
  if (clamped <= timeline.closeMs || timeline.endMs === timeline.closeMs) {
    const sessionSpan = Math.max(1, timeline.closeMs - timeline.startMs);
    return ((clamped - timeline.startMs) / sessionSpan) * timeline.sessionEndPosition;
  }

  const afterCloseSpan = Math.max(1, timeline.endMs - timeline.closeMs);
  const afterCloseProgress = (clamped - timeline.closeMs) / afterCloseSpan;
  return timeline.sessionEndPosition + afterCloseProgress * (100 - timeline.sessionEndPosition);
}

export function clusterByPixelDistance<T>(
  items: T[],
  getPosition: (item: T) => number,
  trackWidth: number,
  minimumDistance: number,
  leadingReserve = 0,
): ReplayMarkerCluster<T>[] {
  if (items.length === 0) return [];

  const minimumPosition = trackWidth > 0
    ? Math.max(0, Math.min(100, (leadingReserve / trackWidth) * 100))
    : 0;
  const sorted = items
    .map((item) => ({ item, position: Math.max(minimumPosition, getPosition(item)) }))
    .toSorted((left, right) => left.position - right.position);
  const clusters: ReplayMarkerCluster<T>[] = [];

  for (const entry of sorted) {
    const previous = clusters.at(-1);
    const distance = previous == null
      ? Number.POSITIVE_INFINITY
      : Math.abs(entry.position - previous.position) * Math.max(0, trackWidth) / 100;

    if (previous && distance < minimumDistance) {
      previous.items.push(entry.item);
      previous.position = entry.position;
    } else {
      clusters.push({ items: [entry.item], position: entry.position });
    }
  }

  return clusters;
}

export function layoutReplayAnnotationTags(
  anchors: number[],
  tagWidth: number,
  plotWidth: number,
  gap: number,
  blockedRanges: ReplayPixelRange[] = [],
): ReplayAnnotationTagLayout[] {
  const width = Math.max(0, tagWidth);
  const availableWidth = Math.max(0, plotWidth);
  const maximumX = Math.max(0, availableWidth - width);
  const spacing = Math.max(0, gap);
  const clampX = (x: number) => Math.max(0, Math.min(maximumX, x));
  const blocked = blockedRanges
    .map((range) => ({
      start: Math.max(0, Math.min(availableWidth, Math.min(range.start, range.end))),
      end: Math.max(0, Math.min(availableWidth, Math.max(range.start, range.end))),
      row: range.row,
    }))
    .filter((range) => range.end > range.start);
  const rows: ReplayPixelRange[][] = [];

  return anchors.map((anchor) => {
    for (let row = 0; ; row += 1) {
      const rowBlocked = blocked.filter((range) => range.row == null || range.row === row);
      const occupied = [...rowBlocked, ...(rows[row] ?? [])];
      const anchorX = clampX(anchor);
      const blockedAtAnchor = rowBlocked.some((range) => (
        anchorX + width + spacing > range.start
        && anchorX < range.end + spacing
        && range.row === row
      ));
      if (blockedAtAnchor) continue;
      const candidates = new Set<number>([clampX(anchor)]);
      occupied.forEach((range) => {
        candidates.add(clampX(range.start - spacing - width));
        candidates.add(clampX(range.end + spacing));
      });
      const x = [...candidates]
        .filter((candidate) => occupied.every((range) => (
          candidate + width + spacing <= range.start
          || candidate >= range.end + spacing
        )))
        .toSorted((left, right) => Math.abs(left - anchor) - Math.abs(right - anchor))[0];

      if (x == null) continue;
      const latestStartInRow = Math.max(Number.NEGATIVE_INFINITY, ...(rows[row] ?? []).map((range) => range.start));
      if (x < latestStartInRow) continue;
      rows[row] ??= [];
      rows[row].push({ start: x, end: x + width });
      return { x, row };
    }
  });
}

export function replayPositionToTime(timeline: ReplayTimeline, position: number): number {
  const clamped = Math.max(0, Math.min(100, position));
  if (clamped <= timeline.sessionEndPosition || timeline.endMs === timeline.closeMs) {
    const sessionProgress = clamped / Math.max(1, timeline.sessionEndPosition);
    return timeline.startMs + sessionProgress * (timeline.closeMs - timeline.startMs);
  }

  const afterCloseProgress = (clamped - timeline.sessionEndPosition)
    / Math.max(1, 100 - timeline.sessionEndPosition);
  return timeline.closeMs + afterCloseProgress * (timeline.endMs - timeline.closeMs);
}

export function formatReplayTime(iso: string, tradingDate: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  const date = `${part('year')}-${part('month')}-${part('day')}`;
  const time = `${part('hour')}:${part('minute')}`;
  if (date === tradingDate) return time;

  const dayMs = 24 * 60 * 60 * 1_000;
  const tradingDay = Date.parse(`${tradingDate}T00:00:00+09:00`);
  const currentDay = Date.parse(`${date}T00:00:00+09:00`);
  const dayDifference = Math.round((currentDay - tradingDay) / dayMs);
  if (dayDifference === -1) return `전일 ${time}`;
  if (dayDifference === 1) return `다음 날 ${time}`;
  return `${Number(date.slice(5, 7))}.${Number(date.slice(8, 10))} ${time}`;
}

export function buildScrubberTicks(data: ReplayData, timeline: ReplayTimeline): string[] {
  const moments = new Set<string>();
  data.states.forEach((item) => moments.add(item.t));
  data.signals.filter((item) => item.status === 'SHOWN').forEach((item) => moments.add(item.at));
  return [...moments]
    .filter((time) => {
      const value = Date.parse(time);
      return value >= timeline.startMs && value <= timeline.endMs;
    })
    .toSorted((left, right) => Date.parse(left) - Date.parse(right));
}

export function buildReplayDensity(
  data: ReplayData,
  timeline: ReplayTimeline,
  bucketCount = 64,
): ReplayDensityBucket[] {
  const count = Math.max(1, Math.floor(bucketCount));
  const values = Array.from({ length: count }, () => 0);
  const moments = [
    ...data.documents.map((item) => Date.parse(item.published_at)),
    ...data.signals.filter((item) => item.status !== 'SHOWN').map((item) => Date.parse(item.at)),
    ...data.log.map((item) => Date.parse(item.t)),
  ];

  moments.forEach((time) => {
    if (time < timeline.startMs || time > timeline.endMs) return;
    const position = replayTimeToPosition(timeline, time);
    const index = Math.min(count - 1, Math.floor((position / 100) * count));
    values[index] += 1;
  });

  const maximum = Math.max(1, ...values);
  return values.map((value, index) => ({
    position: ((index + 0.5) / count) * 100,
    count: value,
    intensity: value / maximum,
  }));
}

export function splitAtSessionOpen<T>(
  items: T[],
  getTime: (item: T) => string,
  sessionOpenMs: number,
  includeSessionOpen = false,
): { premarket: T[]; session: T[] } {
  return items.reduce<{ premarket: T[]; session: T[] }>((groups, item) => {
    const time = Date.parse(getTime(item));
    const key = time < sessionOpenMs || (includeSessionOpen && time === sessionOpenMs)
      ? 'premarket'
      : 'session';
    groups[key].push(item);
    return groups;
  }, { premarket: [], session: [] });
}
