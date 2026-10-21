import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  assembleHeadline,
  aggregateReplayBars,
  advanceReplayPlayhead,
  buildReplayDensity,
  buildReplayEvents,
  buildReplayKeyEvents,
  buildReplayPlaybackPlan,
  buildReplayTimeline,
  buildScrubberTicks,
  clusterByPixelDistance,
  computeStateBands,
  formatReplayTime,
  isReplayData,
  layoutReplayAnnotationTags,
  loadReplayData,
  planReplaySeriesUpdate,
  replayPositionToTime,
  replayTimeToChartCoordinate,
  replayTimeToPosition,
  selectReplaySignalCard,
  selectReplayFrame,
  shouldRecomputeReplayOverlays,
  splitAtSessionOpen,
  visibleBarsAt,
  type ReplayData,
  type ReplayOverlayKey,
} from './replay';

const replay: ReplayData = {
  contract_version: 1,
  symbol: '000660',
  name: 'SK하이닉스',
  date: '2026-10-20',
  prev_close: 100_000,
  thresholds: { enter_pct: 1, exit_pct: 0.5 },
  bars: [
    { t: '2026-10-20T09:00:00+09:00', o: 100_000, h: 100_200, l: 99_900, c: 100_000, v: 10 },
    { t: '2026-10-20T09:24:00+09:00', o: 100_900, h: 101_300, l: 100_800, c: 101_200, v: 20 },
    { t: '2026-10-20T10:10:00+09:00', o: 103_200, h: 103_900, l: 103_000, c: 103_600, v: 30 },
    { t: '2026-10-20T11:53:00+09:00', o: 100_700, h: 100_800, l: 100_200, c: 100_300, v: 40 },
  ],
  states: [
    { t: '2026-10-20T09:24:00+09:00', state: 'POSITIVE', move_id: 'move-1', move_version: 1, return_pct: 1.2 },
    { t: '2026-10-20T11:53:00+09:00', state: 'NEUTRAL', move_id: 'move-1', move_version: 2, return_pct: 0.3 },
  ],
  documents: [
    {
      id: 'doc-1', title: 'HBM 장기 공급 계약', publisher: '산업일보',
      published_at: '2026-10-20T09:50:00+09:00', judged: true, direction: 1,
      scope: 'SYMBOL', fail_reason: null,
    },
    {
      id: 'doc-2', title: '시세 동향', publisher: '경제신문',
      published_at: '2026-10-20T10:20:00+09:00', judged: true, direction: 0,
      scope: 'SYMBOL', fail_reason: 'LISTING_ONLY',
    },
  ],
  signals: [
    {
      at: '2026-10-20T09:24:00+09:00', move_version: 1, version: 1,
      status: 'HIDDEN', headline_label: 'HBM 공급 기대', particle: '로',
      decided_by: null, model: null, prompt_version: 'signal_generation/v2',
      trigger: 'PRICE_STATE_CHANGE', causes: [], decomposition: null,
    },
    {
      at: '2026-10-20T10:10:00+09:00', move_version: 1, version: 2,
      status: 'SHOWN', headline_label: 'HBM 장기 공급 계약', particle: '으로',
      decided_by: 'llm', model: 'model/demo', prompt_version: 'signal_generation/v2',
      trigger: 'DOCUMENT_ARRIVAL',
      causes: [{
        rank: 1, label: 'HBM 공급 계약', explanation: '장기 공급 계약이 확인되며 매수세가 강해졌습니다.',
        scope: 'SYMBOL', source_document_ids: ['doc-1'],
      }],
      decomposition: { global: 0.1, market: 0.2, sector: 0.4, idio: 2.9, dominant_layer: 'IDIO' },
    },
  ],
  log: [
    { t: '2026-10-20T09:24:00+09:00', kind: 'state', text: '+1%를 돌파해 긍정 상태로 전환했습니다.' },
    { t: '2026-10-20T10:10:00+09:00', kind: 'signal', text: '새 기사로 시그널 문구를 갱신했습니다.' },
  ],
};

describe('selectReplayFrame', () => {
  it('selects only bars, documents, signals, and logs known at the playhead', () => {
    const frame = selectReplayFrame(replay, '2026-10-20T10:10:00+09:00');

    expect(frame.bar?.t).toBe('2026-10-20T10:10:00+09:00');
    expect(frame.signal?.version).toBe(2);
    expect(frame.documents.map((document) => document.id)).toEqual(['doc-1']);
    expect(frame.logs).toHaveLength(2);
    expect(frame.latestLog?.kind).toBe('signal');
  });

  it('keeps the latest HIDDEN version visible as the current status', () => {
    const frame = selectReplayFrame(replay, '2026-10-20T09:30:00+09:00');
    expect(frame.signal?.status).toBe('HIDDEN');
    expect(frame.documents).toEqual([]);
  });

  it('reveals prior-day documents and logs at the first market bar', () => {
    const premarketReplay: ReplayData = {
      ...replay,
      documents: [{
        ...replay.documents[0],
        id: 'pre-doc',
        published_at: '2026-10-19T18:20:00+09:00',
      }, ...replay.documents],
      log: [{
        t: '2026-10-19T18:21:00+09:00',
        kind: 'document',
        text: '장 시작 전에 기사를 수집했습니다.',
      }, ...replay.log],
    };

    const frame = selectReplayFrame(premarketReplay, replay.bars[0].t);

    expect(frame.documents.map((document) => document.id)).toContain('pre-doc');
    expect(frame.logs[0].t).toBe('2026-10-19T18:21:00+09:00');
  });

  it('keeps the latest SHOWN signal available when a newer version is hidden', () => {
    const frame = selectReplayFrame(replay, '2026-10-20T11:53:00+09:00');

    expect(frame.signal?.status).toBe('SHOWN');
    expect(frame.shownSignal?.version).toBe(2);
  });
});

describe('selectReplaySignalCard', () => {
  it('hides an older SHOWN signal when the latest version is HIDDEN', () => {
    const hiddenAfterShown: ReplayData = {
      ...replay,
      signals: [
        ...replay.signals,
        {
          ...replay.signals[0],
          at: '2026-10-20T10:20:00+09:00',
          move_version: 1,
          version: 3,
          status: 'HIDDEN',
        },
      ],
    };
    const frame = selectReplayFrame(hiddenAfterShown, '2026-10-20T10:21:00+09:00');

    expect(selectReplaySignalCard(hiddenAfterShown, frame)).toEqual({
      mode: 'empty',
      signal: hiddenAfterShown.signals[2],
    });
  });

  it('shows a pending state after the 14:35 state change and before the 14:37 signal', () => {
    const fixture = JSON.parse(
      readFileSync(new URL('../../public/replay/000660-2026-10-14.json', import.meta.url), 'utf8'),
    ) as ReplayData;
    const frame = selectReplayFrame(fixture, '2026-10-14T14:36:00+09:00');

    expect(selectReplaySignalCard(fixture, frame)).toEqual({
      mode: 'pending',
      signal: fixture.signals[2],
    });
  });

  it('guards against a live return whose sign disagrees with the signal direction', () => {
    const frame = {
      ...selectReplayFrame(replay, '2026-10-20T10:10:00+09:00'),
      returnPct: -1.2,
    };

    expect(selectReplaySignalCard(replay, frame)).toEqual({
      mode: 'pending',
      signal: replay.signals[1],
    });
  });

  it('returns the latest SHOWN signal when state and live return agree', () => {
    const frame = selectReplayFrame(replay, '2026-10-20T10:10:00+09:00');

    expect(selectReplaySignalCard(replay, frame)).toEqual({
      mode: 'shown',
      signal: replay.signals[1],
    });
  });
});

describe('visibleBarsAt', () => {
  it('excludes future prices from chart summaries', () => {
    expect(visibleBarsAt(replay.bars, '2026-10-20T10:10:00+09:00').map((bar) => bar.t)).toEqual([
      '2026-10-20T09:00:00+09:00',
      '2026-10-20T09:24:00+09:00',
      '2026-10-20T10:10:00+09:00',
    ]);
  });
});

describe('aggregateReplayBars', () => {
  const minuteBars = [
    { t: '2026-10-20T09:00:00+09:00', o: 100, h: 103, l: 99, c: 102, v: 10 },
    { t: '2026-10-20T09:01:00+09:00', o: 102, h: 105, l: 101, c: 104, v: 20 },
    { t: '2026-10-20T09:02:00+09:00', o: 104, h: 106, l: 100, c: 101, v: 30 },
    { t: '2026-10-20T09:03:00+09:00', o: 101, h: 104, l: 98, c: 103, v: 40 },
    { t: '2026-10-20T09:04:00+09:00', o: 103, h: 108, l: 102, c: 107, v: 50 },
    { t: '2026-10-20T09:05:00+09:00', o: 107, h: 109, l: 105, c: 106, v: 60 },
  ];

  it('aggregates OHLC and sums volume into three-minute candles', () => {
    expect(aggregateReplayBars(minuteBars, 3)).toEqual([
      { t: '2026-10-20T09:00:00+09:00', o: 100, h: 106, l: 99, c: 101, v: 60 },
      { t: '2026-10-20T09:03:00+09:00', o: 101, h: 109, l: 98, c: 106, v: 150 },
    ]);
  });

  it('keeps the final incomplete five-minute candle', () => {
    expect(aggregateReplayBars(minuteBars, 5)).toEqual([
      { t: '2026-10-20T09:00:00+09:00', o: 100, h: 108, l: 98, c: 107, v: 150 },
      { t: '2026-10-20T09:05:00+09:00', o: 107, h: 109, l: 105, c: 106, v: 60 },
    ]);
  });

  it('keeps clock-aligned buckets when a minute has no trades', () => {
    const sparseBars = [minuteBars[0], minuteBars[2], minuteBars[3], minuteBars[4]];

    expect(aggregateReplayBars(sparseBars, 3)).toEqual([
      { t: '2026-10-20T09:00:00+09:00', o: 100, h: 106, l: 99, c: 101, v: 40 },
      { t: '2026-10-20T09:03:00+09:00', o: 101, h: 108, l: 98, c: 107, v: 90 },
    ]);
  });

  it.each([1, 3, 5] as const)('maps a 09:02 state to its exact chart coordinate for %d-minute bars', (interval) => {
    const sessionBars = Array.from({ length: 16 }, (_, minute) => ({
      t: `2026-10-20T09:${String(minute).padStart(2, '0')}:00+09:00`,
      o: 100,
      h: 101,
      l: 99,
      c: 100,
      v: 1,
    }));
    const aggregated = aggregateReplayBars(sessionBars, interval);
    const coordinate = replayTimeToChartCoordinate(
      aggregated,
      '2026-10-20T09:02:00+09:00',
      150,
    );

    expect(coordinate).toBe(20);
  });
});

describe('clusterByPixelDistance', () => {
  it('combines nearby markers while preserving the latest position in each cluster', () => {
    const markers = [10, 12, 14, 40, 43];

    expect(clusterByPixelDistance(markers, (position) => position, 500, 20)).toEqual([
      { items: [10, 12, 14], position: 14 },
      { items: [40, 43], position: 43 },
    ]);
  });

  it('reserves leading lane space before clustering markers', () => {
    const markers = [1, 4, 20];

    expect(clusterByPixelDistance(markers, (position) => position, 500, 20, 60)).toEqual([
      { items: [1, 4], position: 12 },
      { items: [20], position: 20 },
    ]);
  });
});

describe('layoutReplayAnnotationTags', () => {
  it('keeps tags clear of each other and the playhead label range', () => {
    const blocked = { start: 72, end: 136 };
    const layout = layoutReplayAnnotationTags([48, 64, 152], 48, 220, 8, [blocked]);

    expect(layout).toHaveLength(3);
    layout.forEach((tag) => {
      expect(tag.x).toBeGreaterThanOrEqual(0);
      expect(tag.x + 48).toBeLessThanOrEqual(220);
      expect(tag.x + 48 <= blocked.start || tag.x >= blocked.end).toBe(true);
    });

    layout.forEach((tag, index) => {
      layout.slice(index + 1).forEach((other) => {
        if (tag.row !== other.row) return;
        expect(tag.x + 48 + 8 <= other.x || other.x + 48 + 8 <= tag.x).toBe(true);
      });
    });
  });

  it('stacks a tag when horizontal nudging cannot prevent overlap', () => {
    const layout = layoutReplayAnnotationTags([20, 30, 40], 48, 120, 8);

    expect(layout.some((tag) => tag.row === 1)).toBe(true);
  });

  it('keeps a stacked tag at its anchor when a range blocks only the first row', () => {
    const layout = layoutReplayAnnotationTags([80], 48, 200, 8, [{ start: 72, end: 136, row: 0 }]);

    expect(layout).toEqual([{ x: 80, row: 1 }]);
  });

  it('stacks instead of moving a later tag before an earlier tag', () => {
    const layout = layoutReplayAnnotationTags(
      [60, 90],
      48,
      160,
      8,
      [{ start: 40, end: 152, row: 0 }],
    );

    expect(layout).toEqual([
      { x: 60, row: 1 },
      { x: 90, row: 2 },
    ]);
  });

  it('hides overflow tags when the chart reserves only two rows', () => {
    const layout = layoutReplayAnnotationTags(
      [60, 90],
      48,
      160,
      8,
      [{ start: 40, end: 152, row: 0 }],
      2,
    );

    expect(layout).toEqual([
      { x: 60, row: 1 },
      { x: 90, row: 1, hidden: true },
    ]);
  });
});

describe('planReplaySeriesUpdate', () => {
  const minuteBars = [
    { t: '2026-10-20T09:00:00+09:00', o: 100, h: 103, l: 99, c: 102, v: 10 },
    { t: '2026-10-20T09:01:00+09:00', o: 102, h: 105, l: 101, c: 104, v: 20 },
    { t: '2026-10-20T09:02:00+09:00', o: 104, h: 106, l: 100, c: 101, v: 30 },
    { t: '2026-10-20T09:03:00+09:00', o: 101, h: 104, l: 98, c: 103, v: 40 },
  ];

  it('does not schedule full redraws for repeated animation frames', () => {
    let previous: typeof minuteBars | null = null;
    let setDataCalls = 0;
    let updateCalls = 0;

    for (let frame = 0; frame < 120; frame += 1) {
      const visible = frame < 40 ? minuteBars.slice(0, 1) : frame < 80 ? minuteBars.slice(0, 2) : minuteBars.slice(0, 3);
      const next = aggregateReplayBars(visible, 3);
      const plan = planReplaySeriesUpdate(previous, next);
      if (plan.mode === 'replace') setDataCalls += 1;
      if (plan.mode === 'update') updateCalls += plan.bars.length;
      previous = next;
    }

    expect(setDataCalls).toBe(1);
    expect(updateCalls).toBe(2);
  });

  it('uses incremental updates for appends and the changing final aggregate', () => {
    const first = aggregateReplayBars(minuteBars.slice(0, 1), 3);
    const changedFinal = aggregateReplayBars(minuteBars.slice(0, 2), 3);
    const appended = aggregateReplayBars(minuteBars, 3);

    expect(planReplaySeriesUpdate(first, changedFinal)).toEqual({ mode: 'update', bars: changedFinal });
    expect(planReplaySeriesUpdate(changedFinal, appended)).toEqual({ mode: 'update', bars: appended });
  });

  it('replaces data on load, interval change, and backward seeks', () => {
    const threeMinute = aggregateReplayBars(minuteBars, 3);
    const oneMinute = aggregateReplayBars(minuteBars, 1);

    expect(planReplaySeriesUpdate(null, threeMinute).mode).toBe('replace');
    expect(planReplaySeriesUpdate(threeMinute, oneMinute, true).mode).toBe('replace');
    expect(planReplaySeriesUpdate(threeMinute, threeMinute.slice(0, 1)).mode).toBe('replace');
  });
});

describe('shouldRecomputeReplayOverlays', () => {
  it('recomputes only when an overlay input changes', () => {
    const key: ReplayOverlayKey = {
      width: 884,
      height: 360,
      priceScaleWidth: 74,
      interval: 3,
      revealedStateCount: 1,
      currentBarTime: '2026-10-20T10:10:00+09:00',
      labelOnLeft: false,
    };

    expect(shouldRecomputeReplayOverlays(null, key)).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key })).toBe(false);
    expect(shouldRecomputeReplayOverlays(key, { ...key, currentBarTime: '2026-10-20T10:11:00+09:00' })).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key, revealedStateCount: 2 })).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key, width: 900 })).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key, height: 400 })).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key, priceScaleWidth: 80 })).toBe(true);
    expect(shouldRecomputeReplayOverlays(key, { ...key, interval: 5 })).toBe(true);

    expect(shouldRecomputeReplayOverlays(key, { ...key, labelOnLeft: true })).toBe(true);
  });
});

describe('assembleHeadline', () => {
  it('assembles a Korean rise headline from the live bar return', () => {
    expect(assembleHeadline(replay.signals[1], replay.name, 3.6)).toBe(
      'HBM 장기 공급 계약으로 SK하이닉스 3.6% 상승',
    );
  });

  it('uses the flat form when the return rounds to zero', () => {
    expect(assembleHeadline(replay.signals[1], replay.name, 0.04)).toBe(
      'HBM 장기 공급 계약에도 SK하이닉스 보합',
    );
  });

  it('does not expose a headline for a non-SHOWN signal', () => {
    expect(assembleHeadline(replay.signals[0], replay.name, 1.2)).toBeNull();
  });
});

describe('computeStateBands', () => {
  it('turns state changes into bounded chart bands', () => {
    expect(computeStateBands(replay.states, replay.bars.at(-1)!.t)).toEqual([
      {
        start: '2026-10-20T09:24:00+09:00',
        end: '2026-10-20T11:53:00+09:00',
        state: 'POSITIVE',
      },
      {
        start: '2026-10-20T11:53:00+09:00',
        end: '2026-10-20T11:53:00+09:00',
        state: 'NEUTRAL',
      },
    ]);
  });
});

describe('buildReplayEvents', () => {
  it('deduplicates and sorts all jump-worthy replay moments', () => {
    const events = buildReplayEvents(replay);
    expect(events[0]).toBe('2026-10-20T09:00:00+09:00');
    expect(events).toContain('2026-10-20T09:50:00+09:00');
    expect(events.filter((time) => time === '2026-10-20T10:10:00+09:00')).toHaveLength(1);
    expect(events.at(-1)).toBe('2026-10-20T11:53:00+09:00');
  });
});

describe('buildReplayKeyEvents', () => {
  it('lists state changes, visible signal versions, and KEEP decisions without articles', () => {
    const keyEventReplay: ReplayData = {
      ...replay,
      signals: [
        ...replay.signals,
        {
          ...replay.signals[1],
          at: '2026-10-20T10:20:00+09:00',
          version: 3,
          status: 'NO_CANDIDATE',
        },
      ],
      log: [
        ...replay.log,
        { t: '2026-10-20T10:30:00+09:00', kind: 'signal', text: '기존 문구 유지: 새 기사가 기존 원인과 같습니다.' },
      ],
    };

    expect(buildReplayKeyEvents(keyEventReplay)).toEqual([
      {
        id: 'state-2026-10-20T09:24:00+09:00-POSITIVE-1',
        at: '2026-10-20T09:24:00+09:00',
        kind: 'state',
        label: '긍정 진입',
      },
      {
        id: 'signal-2026-10-20T09:24:00+09:00-1-HIDDEN',
        at: '2026-10-20T09:24:00+09:00',
        kind: 'signal',
        label: '시그널 v1',
      },
      {
        id: 'signal-2026-10-20T10:10:00+09:00-2-SHOWN',
        at: '2026-10-20T10:10:00+09:00',
        kind: 'signal',
        label: '시그널 v2',
        headline: 'HBM 장기 공급 계약',
      },
      {
        id: 'keep-2026-10-20T10:30:00+09:00-2',
        at: '2026-10-20T10:30:00+09:00',
        kind: 'keep',
        label: '문구 유지',
        headline: '새 기사가 기존 원인과 같습니다.',
      },
      {
        id: 'state-2026-10-20T11:53:00+09:00-NEUTRAL-2',
        at: '2026-10-20T11:53:00+09:00',
        kind: 'state',
        label: '중립 전환',
      },
    ]);
  });
});

describe('advanceReplayPlayhead', () => {
  const keyEvents = buildReplayKeyEvents(replay);
  const startMs = Date.parse('2026-10-20T09:00:00+09:00');
  const endMs = Date.parse('2026-10-20T11:53:00+09:00');
  const playbackPlan = buildReplayPlaybackPlan(keyEvents, startMs, endMs);

  it('uses the selected speed from five minutes before through ten minutes after an event', () => {
    const currentMs = Date.parse('2026-10-20T10:06:00+09:00');
    const result = advanceReplayPlayhead(currentMs, 1_000, 4, playbackPlan, true);

    expect(result).toEqual({
      nextMs: Date.parse('2026-10-20T10:10:00+09:00'),
      fastForwarding: false,
    });
  });

  it('compresses a complete idle segment to about two and a half seconds', () => {
    const idleStartMs = Date.parse('2026-10-20T09:34:00+09:00');
    const result = advanceReplayPlayhead(idleStartMs, 2_500, 4, playbackPlan, true);

    expect(result).toEqual({
      nextMs: Date.parse('2026-10-20T10:05:00+09:00'),
      fastForwarding: true,
    });
  });

  it('uses the selected speed throughout when idle skipping is off', () => {
    const idleStartMs = Date.parse('2026-10-20T09:34:00+09:00');
    const result = advanceReplayPlayhead(idleStartMs, 1_000, 4, playbackPlan, false);

    expect(result).toEqual({
      nextMs: Date.parse('2026-10-20T09:38:00+09:00'),
      fastForwarding: false,
    });
  });

  it('consumes remaining wall time after crossing a playback window boundary', () => {
    const boundaryPlan = buildReplayPlaybackPlan([
      {
        id: 'signal-2026-10-20T09:10:00+09:00-1-SHOWN',
        at: '2026-10-20T09:10:00+09:00',
        kind: 'signal',
        label: '시그널 v1',
      },
    ], startMs, Date.parse('2026-10-20T10:00:00+09:00'));

    expect(advanceReplayPlayhead(
      Date.parse('2026-10-20T09:19:00+09:00'),
      2_000,
      1,
      boundaryPlan,
      true,
    )).toEqual({
      nextMs: Date.parse('2026-10-20T09:36:00+09:00'),
      fastForwarding: true,
    });
  });
});

describe('replay timeline', () => {
  const afterCloseReplay: ReplayData = {
    ...replay,
    documents: [
      ...replay.documents,
      {
        ...replay.documents[0],
        id: 'after-close-doc',
        published_at: '2026-10-20T18:00:00+09:00',
      },
    ],
    signals: [
      ...replay.signals,
      {
        ...replay.signals[1],
        at: '2026-10-20T17:00:00+09:00',
        version: 3,
      },
    ],
  };

  it('starts at the first bar and reserves a compressed segment after the last bar', () => {
    const timeline = buildReplayTimeline(afterCloseReplay);

    expect(timeline.startMs).toBe(Date.parse(replay.bars[0].t));
    expect(timeline.closeMs).toBe(Date.parse(replay.bars.at(-1)!.t));
    expect(timeline.endMs).toBe(Date.parse('2026-10-20T18:00:00+09:00'));
    expect(replayTimeToPosition(timeline, timeline.startMs)).toBe(0);
    expect(replayTimeToPosition(timeline, timeline.closeMs)).toBe(82);
    expect(replayTimeToPosition(timeline, timeline.endMs)).toBe(100);
  });

  it('maps scrubber positions back to session and after-close times', () => {
    const timeline = buildReplayTimeline(afterCloseReplay);
    const sessionMiddle = timeline.startMs + (timeline.closeMs - timeline.startMs) / 2;
    const afterCloseMiddle = timeline.closeMs + (timeline.endMs - timeline.closeMs) / 2;

    expect(replayPositionToTime(timeline, 41)).toBe(sessionMiddle);
    expect(replayPositionToTime(timeline, 91)).toBe(afterCloseMiddle);
  });

  it('uses the full scrubber when no events occur after the close', () => {
    const timeline = buildReplayTimeline(replay);

    expect(timeline.sessionEndPosition).toBe(100);
    expect(replayTimeToPosition(timeline, timeline.closeMs)).toBe(100);
  });
});

describe('replay timeline presentation', () => {
  it('disambiguates times outside the trading date', () => {
    expect(formatReplayTime('2026-10-19T18:20:00+09:00', replay.date)).toBe('전일 18:20');
    expect(formatReplayTime('2026-10-20T09:00:00+09:00', replay.date)).toBe('09:00');
    expect(formatReplayTime('2026-10-21T08:10:00+09:00', replay.date)).toBe('다음 날 08:10');
  });

  it('uses only state changes and SHOWN signal versions as scrubber ticks', () => {
    const timeline = buildReplayTimeline(replay);

    expect(buildScrubberTicks(replay, timeline)).toEqual([
      '2026-10-20T09:24:00+09:00',
      '2026-10-20T10:10:00+09:00',
      '2026-10-20T11:53:00+09:00',
    ]);
  });

  it('bins documents, logs, and non-SHOWN signals into a faint density strip', () => {
    const timeline = buildReplayTimeline(replay);
    const density = buildReplayDensity(replay, timeline, 8);

    expect(density).toHaveLength(8);
    expect(density.reduce((sum, bucket) => sum + bucket.count, 0)).toBe(5);
    expect(density.some((bucket) => bucket.intensity === 1)).toBe(true);
  });

  it('separates items published before the first bar into the premarket group', () => {
    const items = [
      { t: '2026-10-19T18:20:00+09:00' },
      { t: '2026-10-20T08:59:00+09:00' },
      { t: '2026-10-20T09:00:00+09:00' },
    ];

    expect(splitAtSessionOpen(items, (item) => item.t, Date.parse(replay.bars[0].t))).toEqual({
      premarket: items.slice(0, 2),
      session: items.slice(2),
    });
  });

  it('can include items stamped at market open in the opening batch', () => {
    const items = [
      { t: '2026-10-20T08:59:00+09:00' },
      { t: '2026-10-20T09:00:00+09:00' },
      { t: '2026-10-20T09:01:00+09:00' },
    ];

    expect(splitAtSessionOpen(items, (item) => item.t, Date.parse(replay.bars[0].t), true)).toEqual({
      premarket: items.slice(0, 2),
      session: items.slice(2),
    });
  });
});

describe('loadReplayData', () => {
  it('falls back to the fixture when the dev server returns the app HTML for a missing JSON file', async () => {
    const requested: string[] = [];
    const request = async (input: string | URL | Request): Promise<Response> => {
      const url = String(input);
      requested.push(url);
      if (url.includes('000660-2026-10-20.json')) {
        return new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } });
      }
      return Response.json(replay);
    };

    const result = await loadReplayData('000660', '2026-10-20', undefined, request);

    expect(requested).toEqual([
      '/replay/000660-2026-10-20.json',
      '/replay/demo-fixture.json',
    ]);
    expect(result.fixture).toBe(true);
    expect(result.replay.symbol).toBe('000660');
  });
});

describe('isReplayData', () => {
  it('accepts valid replay data', () => {
    expect(isReplayData(replay)).toBe(true);
  });

  it('rejects malformed nested values and unbounded chart ranges', () => {
    expect(isReplayData({ ...replay, bars: [{ ...replay.bars[0], c: Number.NaN }] })).toBe(false);
    expect(isReplayData({
      ...replay,
      bars: [
        replay.bars[0],
        { ...replay.bars[1], t: '2026-10-22T09:24:00+09:00' },
      ],
    })).toBe(false);
  });
});

describe('demo fixture contract', () => {
  const fixture = JSON.parse(
    readFileSync(new URL('../../public/replay/demo-fixture.json', import.meta.url), 'utf8'),
  ) as ReplayData;

  it('contains every one-minute bar from market open through close', () => {
    expect(fixture.bars).toHaveLength(391);
    expect(fixture.bars[0].t).toBe('2026-10-20T09:00:00+09:00');
    expect(fixture.bars.at(-1)?.t).toBe('2026-10-20T15:30:00+09:00');
    for (let index = 1; index < fixture.bars.length; index += 1) {
      expect(Date.parse(fixture.bars[index].t) - Date.parse(fixture.bars[index - 1].t)).toBe(60_000);
    }
  });

  it('never cites a document before its publication time', () => {
    const publishedAt = new Map(fixture.documents.map((document) => [document.id, Date.parse(document.published_at)]));
    for (const signal of fixture.signals) {
      for (const cause of signal.causes) {
        for (const documentId of cause.source_document_ids) {
          expect(publishedAt.get(documentId), documentId).toBeLessThanOrEqual(Date.parse(signal.at));
        }
      }
    }
  });
});
