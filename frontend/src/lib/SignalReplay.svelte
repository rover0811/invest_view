<script lang="ts">
  import { untrack } from 'svelte';
  import { fmtPrice } from './format';
  import ReplayChart from './ReplayChart.svelte';
  import {
    advanceReplayPlayhead,
    assembleHeadline,
    buildReplayDensity,
    buildReplayEvents,
    buildReplayKeyEvents,
    buildReplayPlaybackPlan,
    buildReplayTimeline,
    buildScrubberTicks,
    clusterByPixelDistance,
    formatReplayTime,
    loadReplayData,
    replayPositionToTime,
    replayTimeToPosition,
    selectReplayFrame,
    splitAtSessionOpen,
    type PriceState,
    type ReplayData,
    type ReplayDocument,
    type ReplayKeyEvent,
  } from './replay';

  let { symbol, date }: { symbol: string; date: string } = $props();

  let data = $state<ReplayData | null>(null);
  let loading = $state(true);
  let loadError = $state('');
  let fixture = $state(false);
  let playheadMs = $state(0);
  let playing = $state(false);
  let speed = $state(4);
  let skipIdle = $state(true);
  let fastForwarding = $state(false);
  let animatedPlayheadMs = 0;
  let driveChartPlayhead = (_playheadMs: number) => {};
  let laneTrackElement = $state<HTMLDivElement>();
  let laneTrackWidth = $state(0);
  let markerDistance = $state(0);
  let premarketReserve = $state(0);
  let keyEventListElement = $state<HTMLOListElement>();

  const timeline = $derived(data ? buildReplayTimeline(data) : null);
  const startMs = $derived(timeline?.startMs ?? 0);
  const closeMs = $derived(timeline?.closeMs ?? 0);
  const endMs = $derived(timeline?.endMs ?? 0);
  const events = $derived(data && timeline
    ? buildReplayEvents(data).filter((event) => Date.parse(event) >= timeline.startMs)
    : []);
  const keyEvents = $derived(data && timeline
    ? buildReplayKeyEvents(data).filter((event) => {
        const time = Date.parse(event.at);
        return time >= timeline.startMs && time <= timeline.endMs;
      })
    : []);
  const currentKeyEventId = $derived(
    keyEvents.findLast((event) => Date.parse(event.at) <= playheadMs)?.id ?? null,
  );
  const playbackPlan = $derived(buildReplayPlaybackPlan(keyEvents, startMs, endMs));
  const scrubberTicks = $derived(data && timeline ? buildScrubberTicks(data, timeline) : []);
  const density = $derived(data && timeline ? buildReplayDensity(data, timeline) : []);
  const playhead = $derived(new Date(playheadMs || startMs).toISOString());
  const frame = $derived(data ? selectReplayFrame(data, playhead) : null);
  const shownSignal = $derived(frame?.shownSignal);
  const headline = $derived(data && frame ? assembleHeadline(shownSignal, data.name, frame.returnPct) : null);
  const playheadPosition = $derived(timeline ? replayTimeToPosition(timeline, playheadMs) : 0);
  const afterClose = $derived(closeMs > 0 && playheadMs > closeMs);
  const documentGroups = $derived(frame
    ? splitAtSessionOpen(frame.documents, (document) => document.published_at, startMs)
    : { premarket: [], session: [] });
  const logGroups = $derived(frame
    ? splitAtSessionOpen(frame.logs, (entry) => entry.t, startMs, true)
    : { premarket: [], session: [] });
  const barsOnly = $derived(data != null
    && data.states.length === 0
    && data.documents.length === 0
    && data.signals.length === 0);

  const stateMeta: Record<PriceState, { label: string; className: string }> = {
    POSITIVE: { label: '긍정', className: 'positive' },
    NEUTRAL: { label: '중립', className: 'neutral' },
    NEGATIVE: { label: '부정', className: 'negative' },
  };

  const logLabels: Record<string, string> = {
    document: '기사',
    judgement: '판단',
    state: '상태',
    request: '요청',
    llm: 'LLM',
    fallback: '대체',
    signal: '시그널',
  };

  function formatTime(iso: string): string {
    return formatReplayTime(iso, data?.date ?? date);
  }

  function formatDate(value: string): string {
    const [year, month, day] = value.split('-');
    return `${year}.${month}.${day}`;
  }

  function priceClass(rate: number): string {
    if (Math.abs(rate) < 0.05) return 'price-flat';
    return rate > 0 ? 'price-up' : 'price-down';
  }

  function documentLane(document: ReplayDocument): 'positive' | 'neutral' | 'negative' | 'excluded' {
    if (document.fail_reason != null) return 'excluded';
    if (document.direction === 1) return 'positive';
    if (document.direction === -1) return 'negative';
    return 'neutral';
  }

  function documentPosition(document: ReplayDocument): number {
    return timeline ? replayTimeToPosition(timeline, Date.parse(document.published_at)) : 0;
  }

  function eventPosition(event: string): number {
    return timeline ? replayTimeToPosition(timeline, Date.parse(event)) : 0;
  }

  function keyEventClass(event: ReplayKeyEvent): string {
    if (event.kind !== 'state') return event.kind;
    if (event.label === '긍정 진입') return 'positive';
    if (event.label === '부정 진입') return 'negative';
    return 'neutral';
  }

  function keyEventAriaLabel(event: ReplayKeyEvent): string {
    return [formatTime(event.at), event.label, event.headline].filter(Boolean).join(' ');
  }

  function causeRank(documentId: string): number | undefined {
    return shownSignal?.causes.find((cause) => cause.source_document_ids.includes(documentId))?.rank;
  }

  function featuredLaneDocument(documents: ReplayDocument[]): ReplayDocument | undefined {
    const ranked = documents
      .map((document) => ({ document, rank: causeRank(document.id) }))
      .filter((item): item is { document: ReplayDocument; rank: number } => item.rank != null)
      .toSorted((left, right) => left.rank - right.rank);
    return ranked[0]?.document ?? documents.at(-1);
  }

  function sourceDocuments(documentIds: string[]): ReplayDocument[] {
    if (!frame) return [];
    const ids = new Set(documentIds);
    return frame.documents.filter((document) => ids.has(document.id));
  }

  function registerPlayheadFrame(driver: ((playheadMs: number) => void) | null) {
    driveChartPlayhead = driver ?? (() => {});
    if (driver && animatedPlayheadMs > 0) driver(animatedPlayheadMs);
  }

  $effect(() => {
    if (!laneTrackElement) return;
    const track = laneTrackElement;
    const styles = getComputedStyle(document.documentElement);
    const space6 = Number.parseFloat(styles.getPropertyValue('--space-6'));
    const space8 = Number.parseFloat(styles.getPropertyValue('--space-8'));
    const space3 = Number.parseFloat(styles.getPropertyValue('--space-3'));
    markerDistance = space6 + space6;
    premarketReserve = space8 + space6 + space3;
    const measure = () => {
      laneTrackWidth = track.clientWidth;
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    return () => observer.disconnect();
  });

  $effect(() => {
    const currentId = currentKeyEventId;
    if (!keyEventListElement || !currentId) return;
    const list = keyEventListElement;
    const frame = requestAnimationFrame(() => {
      const current = list.querySelector<HTMLElement>('[aria-current="true"]');
      if (!current) return;
      const itemLeft = current.offsetLeft;
      const itemRight = itemLeft + current.offsetWidth;
      if (itemLeft < list.scrollLeft) list.scrollLeft = itemLeft;
      else if (itemRight > list.scrollLeft + list.clientWidth) {
        list.scrollLeft = itemRight - list.clientWidth;
      }
    });
    return () => cancelAnimationFrame(frame);
  });

  function setPlayhead(nextPlayheadMs: number) {
    animatedPlayheadMs = nextPlayheadMs;
    playheadMs = nextPlayheadMs;
    driveChartPlayhead(nextPlayheadMs);
  }

  function latestMomentIndex(moments: number[], target: number): number {
    let low = 0;
    let high = moments.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (moments[middle] <= target) low = middle + 1;
      else high = middle;
    }
    return low - 1;
  }

  function contentMoments(replay: ReplayData): number[] {
    return [
      ...replay.bars.map((item) => Date.parse(item.t)),
      ...replay.states.map((item) => Date.parse(item.t)),
      ...replay.documents.map((item) => Date.parse(item.published_at)),
      ...replay.signals.map((item) => Date.parse(item.at)),
      ...replay.log.map((item) => Date.parse(item.t)),
    ].toSorted((left, right) => left - right);
  }

  function jump(direction: -1 | 1) {
    if (events.length === 0) return;
    const current = playheadMs;
    const candidates = direction < 0
      ? events.filter((event) => Date.parse(event) < current).toReversed()
      : events.filter((event) => Date.parse(event) > current);
    const target = candidates[0] ?? (direction < 0 ? events[0] : events[events.length - 1]);
    setPlayhead(Date.parse(target));
    playing = false;
  }

  function jumpTo(event: string) {
    setPlayhead(Date.parse(event));
    playing = false;
  }

  function togglePlayback() {
    if (playheadMs >= endMs && endMs > 0) setPlayhead(startMs);
    playing = !playing;
  }

  function updateRange(event: Event) {
    if (!timeline) return;
    const mappedTime = replayPositionToTime(timeline, Number((event.currentTarget as HTMLInputElement).value));
    setPlayhead(Math.round(mappedTime / 60_000) * 60_000);
    playing = false;
  }

  function isEditableTarget(target: EventTarget | null): boolean {
    return target instanceof HTMLInputElement
      || target instanceof HTMLTextAreaElement
      || target instanceof HTMLSelectElement
      || target instanceof HTMLButtonElement;
  }

  $effect(() => {
    const controller = new AbortController();
    loading = true;
    loadError = '';
    data = null;
    playing = false;

    loadReplayData(symbol, date, controller.signal)
      .then((result) => {
        data = result.replay;
        fixture = result.fixture;
        setPlayhead(Date.parse(result.replay.bars[0].t));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        loadError = error instanceof Error ? error.message : '재생 데이터를 불러오지 못했습니다.';
      })
      .finally(() => {
        if (!controller.signal.aborted) loading = false;
      });

    return () => controller.abort();
  });

  $effect(() => {
    if (!playing || !data || endMs <= startMs) {
      fastForwarding = false;
      return;
    }
    const replay = data;
    const playbackSpeed = speed;
    const skippingIdle = skipIdle;
    const currentPlaybackPlan = playbackPlan;
    const moments = contentMoments(replay);
    animatedPlayheadMs = Math.max(startMs, Math.min(endMs, untrack(() => animatedPlayheadMs || playheadMs)));
    let committedMomentIndex = latestMomentIndex(moments, animatedPlayheadMs);
    let animationFrame = 0;
    let previous = performance.now();

    function tick(now: number) {
      const elapsed = now - previous;
      previous = now;
      const advance = advanceReplayPlayhead(
        animatedPlayheadMs,
        elapsed,
        playbackSpeed,
        currentPlaybackPlan,
        skippingIdle,
      );
      animatedPlayheadMs = advance.nextMs;
      fastForwarding = advance.fastForwarding;
      driveChartPlayhead(animatedPlayheadMs);

      const nextMomentIndex = latestMomentIndex(moments, animatedPlayheadMs);
      if (nextMomentIndex !== committedMomentIndex) {
        committedMomentIndex = nextMomentIndex;
        playheadMs = animatedPlayheadMs;
      }

      if (animatedPlayheadMs >= endMs) {
        playheadMs = endMs;
        fastForwarding = false;
        playing = false;
        return;
      }
      animationFrame = requestAnimationFrame(tick);
    }

    animationFrame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(animationFrame);
      fastForwarding = false;
    };
  });

  $effect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (isEditableTarget(event.target)) return;
      if (event.code === 'Space') {
        event.preventDefault();
        togglePlayback();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        jump(-1);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        jump(1);
      }
    }

    window.addEventListener('keydown', handleKeydown);
    return () => window.removeEventListener('keydown', handleKeydown);
  });
</script>

<div class="replay-page">
  {#if loading}
    <section class="status-panel panel" aria-live="polite">
      <span class="status-kicker">시그널 리플레이</span>
      <strong>재생 데이터를 불러오고 있습니다.</strong>
    </section>
  {:else if loadError}
    <section class="status-panel panel" role="alert">
      <span class="status-kicker">시그널 리플레이</span>
      <strong>{loadError}</strong>
      <span>주소의 종목 코드와 거래일을 확인해 주세요.</span>
    </section>
  {:else if data && frame}
    <header class="replay-header">
      <div class="header-copy">
        <div class="header-meta">
          <span>시그널 리플레이</span>
          <span class="header-separator" aria-hidden="true"></span>
          <time class="tnum">{formatDate(data.date)}</time>
          {#if fixture}<span class="fixture-badge">데모 데이터</span>{/if}
        </div>
        <h1>{data.name}의 하루를 사건 순서대로 다시 봅니다.</h1>
        <p>가격 상태와 기사 판단, 시그널 버전이 바뀐 시점을 한 화면에서 확인할 수 있습니다.</p>
      </div>
      <div class="header-price">
        <span class="symbol tnum">{data.symbol}</span>
        <strong class="tnum {priceClass(frame.returnPct)}">{frame.bar ? fmtPrice(frame.bar.c) : fmtPrice(data.prev_close)}</strong>
        <span class="tnum {priceClass(frame.returnPct)}">{frame.returnPct >= 0 ? '+' : ''}{frame.returnPct.toFixed(1)}%</span>
      </div>
    </header>

    <div class="replay-grid">
      <div class="replay-main">
        <ReplayChart {data} {playhead} {registerPlayheadFrame} />

        <section class="lanes panel" aria-label="상태별 기사 윈도우">
          <header class="section-head">
            <div>
              <span class="section-kicker">상태 윈도우</span>
              <strong>현재 상태와 같은 방향의 사건만 원인 후보가 됩니다.</strong>
            </div>
            <span class="document-count tnum">공개된 기사 {frame.documents.length}건</span>
          </header>
          <div class="lanes-scroll">
            <div
              class="lanes-canvas"
              class:empty={frame.documents.length === 0}
              style="--session-end: {timeline?.sessionEndPosition ?? 100}%;"
            >
              {#if frame.documents.length === 0}
                <div class="lanes-empty">
                  <strong>{data.documents.length === 0 ? '이 거래일에는 연결된 기사가 없습니다.' : '이 시점까지 공개된 기사가 없습니다.'}</strong>
                  <span>{data.documents.length === 0 ? '가격 흐름은 그대로 재생되며, 기사 판단이 추가되면 이곳에 표시됩니다.' : '재생을 계속하면 공개 시점에 맞춰 기사가 표시됩니다.'}</span>
                </div>
              {:else}
                {#each [
                  { key: 'positive', label: '긍정 윈도우' },
                  { key: 'neutral', label: '중립 윈도우' },
                  { key: 'negative', label: '부정 윈도우' },
                  { key: 'excluded', label: '제외' },
                ] as lane}
                  {@const premarketDocuments = documentGroups.premarket
                    .filter((document) => documentLane(document) === lane.key)
                    .toSorted((a, b) => Date.parse(a.published_at) - Date.parse(b.published_at))}
                  {@const laneDocuments = documentGroups.session
                    .filter((document) => documentLane(document) === lane.key)
                    .toSorted((a, b) => Date.parse(a.published_at) - Date.parse(b.published_at))}
                  {@const featuredDocument = featuredLaneDocument(laneDocuments)}
                  {@const documentClusters = clusterByPixelDistance(
                    laneDocuments,
                    documentPosition,
                    laneTrackWidth,
                    markerDistance,
                    premarketDocuments.length > 0 ? premarketReserve : 0,
                  )}
                  <div class="lane {lane.key}" class:active={frame.state && stateMeta[frame.state.state].className === lane.key}>
                    <span class="lane-label">{lane.label}</span>
                    <div class="lane-track" bind:this={laneTrackElement}>
                      {#if premarketDocuments.length > 0}
                        <details class="premarket-group">
                          <summary aria-label={`장 전 기사 ${premarketDocuments.length}건`}>
                            <span>장 전</span>
                            <strong class="tnum">{premarketDocuments.length}</strong>
                          </summary>
                          <div class="premarket-documents">
                            {#each premarketDocuments.slice(-3).toReversed() as document}
                              <div>
                                <time class="tnum">{formatTime(document.published_at)}</time>
                                <span>{document.title}</span>
                              </div>
                            {/each}
                            {#if premarketDocuments.length > 3}
                              <p>그 밖의 기사 {premarketDocuments.length - 3}건이 장 시작 전에 공개되었습니다.</p>
                            {/if}
                          </div>
                        </details>
                      {/if}
                      {#each documentClusters as cluster}
                        {@const representative = cluster.items.at(-1)!}
                        {@const clusterFeatured = featuredDocument != null
                          && cluster.items.some((document) => document.id === featuredDocument.id)}
                        {@const rank = clusterFeatured ? causeRank(featuredDocument.id) : undefined}
                        <button
                          type="button"
                          class="document-marker clustered"
                          class:ranked={rank != null}
                          class:featured={clusterFeatured}
                          class:align-right={cluster.position > 65}
                          style="--position: {cluster.position}%;"
                          aria-label={cluster.items.length > 1
                            ? `${formatTime(representative.published_at)} 주변 기사 ${cluster.items.length}건`
                            : `${formatTime(representative.published_at)} ${representative.title}`}
                        >
                          <span class="marker-dot">{cluster.items.length > 1 ? cluster.items.length : rank ?? ''}</span>
                          {#if clusterFeatured}
                            <span class="marker-label">{featuredDocument.title}</span>
                          {/if}
                          <span class="document-tooltip" role="tooltip">
                            <strong>기사 {cluster.items.length}건</strong>
                            <span class="tooltip-documents">
                              {#each cluster.items.toReversed() as document}
                                <span class="tooltip-document">
                                  <time class="tnum">{formatTime(document.published_at)}</time>
                                  <span>{document.title}</span>
                                </span>
                              {/each}
                            </span>
                          </span>
                        </button>
                      {/each}
                    </div>
                  </div>
                {/each}
                <div class="lane-playhead" style="left: {playheadPosition}%;" aria-hidden="true"></div>
              {/if}
            </div>
          </div>
        </section>
      </div>

      <aside class="replay-aside">
        <section class="signal-card panel" aria-live="polite">
          <header class="signal-head">
            <span class="signal-time">
              <time class="tnum">{formatTime(playhead)}</time>
              {#if afterClose}<span class="after-close-badge">장 마감 뒤</span>{/if}
            </span>
            {#if shownSignal}
              <span class="version-badge tnum">v{shownSignal.version} SHOWN</span>
            {:else}
              <span class="version-badge">대기 중</span>
            {/if}
          </header>

          <div class="live-price">
            <strong>{data.name}</strong>
            <span class="tnum {priceClass(frame.returnPct)}">{frame.returnPct >= 0 ? '+' : ''}{frame.returnPct.toFixed(1)}%</span>
          </div>

          {#if shownSignal && headline}
            <h2>{headline}</h2>
            <ol class="cause-list">
              {#each shownSignal.causes.toSorted((a, b) => a.rank - b.rank) as cause}
                {@const sources = sourceDocuments(cause.source_document_ids)}
                <li>
                  <span class="cause-rank tnum">{cause.rank}</span>
                  <div>
                    <strong>{cause.label}</strong>
                    <p>{cause.explanation}</p>
                    <span class="source-count">
                      {cause.source_document_ids.length > 0
                        ? `기사 ${cause.source_document_ids.length}건을 근거로 사용했습니다.`
                        : '장중 수급을 근거로 사용했습니다.'}
                    </span>
                    {#if sources.length > 0}
                      <ul class="source-list" aria-label={`${cause.label} 근거 기사`}>
                        {#each sources.slice(0, 2) as source}
                          <li>
                            <span>{source.publisher}</span>
                            <strong>{source.title}</strong>
                          </li>
                        {/each}
                      </ul>
                    {/if}
                  </div>
                </li>
              {/each}
            </ol>
          {:else if frame.signal?.status === 'HIDDEN'}
            <div class="hidden-state">
              <strong>이전 시그널을 숨겼습니다.</strong>
              <p>가격 방향이 바뀌어 새 상태의 사건으로 문구를 다시 만드는 중입니다.</p>
            </div>
          {:else if barsOnly}
            <div class="hidden-state">
              <strong>이 거래일에는 생성된 시그널이 없습니다.</strong>
              <p>가격 흐름은 차트에서 그대로 확인할 수 있으며, 상태와 기사가 추가되면 시그널도 함께 표시됩니다.</p>
            </div>
          {:else}
            <div class="hidden-state">
              <strong>아직 표시할 시그널이 없습니다.</strong>
              <p>가격이 진입 기준을 넘거나 새로운 원인 후보가 도착하면 문구를 생성합니다.</p>
            </div>
          {/if}

          {#if shownSignal}
            <dl class="signal-meta">
              <div><dt>생성 방식</dt><dd>{shownSignal.decided_by === 'llm' ? 'LLM 판단' : shownSignal.trigger.includes('FALLBACK') ? '대체 규칙' : '상태 규칙'}</dd></div>
              <div><dt>생성 계기</dt><dd>{shownSignal.trigger}</dd></div>
              <div><dt>프롬프트</dt><dd>{shownSignal.prompt_version}</dd></div>
              <div><dt>모델</dt><dd>{shownSignal.model ?? '사용하지 않음'}</dd></div>
            </dl>
          {/if}
        </section>

        <section class="log-panel panel">
          <header class="section-head log-head">
            <div>
              <span class="section-kicker">처리 기록</span>
              <strong>재생 시점까지 완료된 단계입니다.</strong>
            </div>
          </header>
          {#if frame.logs.length > 0}
            <div class="log-content">
              {#if logGroups.premarket.length > 0}
                <details class="premarket-log">
                  <summary>
                    <span>장 전 처리 기록</span>
                    <strong class="tnum">{logGroups.premarket.length}건</strong>
                    <span class="summary-action">펼치기</span>
                  </summary>
                  <ol class="log-list premarket-log-list">
                    {#each logGroups.premarket.toReversed() as entry}
                      <li>
                        <time class="tnum">{formatTime(entry.t)}</time>
                        <span class="log-kind">{logLabels[entry.kind]}</span>
                        <p>{entry.text}</p>
                      </li>
                    {/each}
                  </ol>
                </details>
              {/if}
              <ol class="log-list">
              {#each logGroups.session.toReversed() as entry, index}
                <li class:newest={index === 0}>
                  <time class="tnum">{formatTime(entry.t)}</time>
                  <span class="log-kind">{logLabels[entry.kind]}</span>
                  <p>{entry.text}</p>
                </li>
              {/each}
              </ol>
            </div>
          {:else}
            <p class="empty-log">이 시점까지 완료된 처리 단계가 없습니다.</p>
          {/if}
        </section>
      </aside>
    </div>

    <section class="playback panel" aria-label="재생 제어">
      <div class="key-events">
        <div class="key-events-title">
          <strong>핵심 사건</strong>
          <span class="tnum">{keyEvents.length}건</span>
        </div>
        <ol class="key-event-list" bind:this={keyEventListElement} aria-label="핵심 사건 목록">
          {#each keyEvents as event (event.id)}
            <li>
              <button
                type="button"
                class:current={event.id === currentKeyEventId}
                aria-current={event.id === currentKeyEventId ? 'true' : undefined}
                aria-label={`${keyEventAriaLabel(event)} 시점으로 이동`}
                onclick={() => jumpTo(event.at)}
              >
                <time class="tnum">{formatTime(event.at)}</time>
                <span class="key-event-kind {keyEventClass(event)}">{event.label}</span>
                {#if event.headline}<span class="key-event-headline">{event.headline}</span>{/if}
              </button>
            </li>
          {/each}
        </ol>
        <label class="skip-toggle">
          <input type="checkbox" bind:checked={skipIdle} />
          <span class="toggle-track" aria-hidden="true"><span></span></span>
          <span>빈 구간 건너뛰기</span>
        </label>
      </div>

      <div class="transport">
        <button type="button" class="icon-button" onclick={() => jump(-1)} aria-label="이전 사건으로 이동">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4v12M15 5l-7 5 7 5z" /></svg>
        </button>
        <button type="button" class="play-button" onclick={togglePlayback} aria-label={playing ? '일시정지' : '재생'} aria-pressed={playing}>
          {#if playing}
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4h3v12H6zM11 4h3v12h-3z" /></svg>
          {:else}
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4l9 6-9 6z" /></svg>
          {/if}
        </button>
        <button type="button" class="icon-button" onclick={() => jump(1)} aria-label="다음 사건으로 이동">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15 4v12M5 5l7 5-7 5z" /></svg>
        </button>
      </div>

      <div class="scrubber-wrap">
        <div class="scrubber-meta">
          <time class="tnum current-time">{formatTime(playhead)}{afterClose ? ' 장 마감 뒤' : ''}</time>
          <span class:fast-forwarding={fastForwarding} aria-live="polite">
            {fastForwarding ? '다음 사건까지 빠르게 넘기는 중' : '09:00-15:30 장중, 이후 구간은 압축하여 표시합니다.'}
          </span>
          <time class="tnum">{formatTime(new Date(endMs).toISOString())}</time>
        </div>
        <div class="scrubber" style="--session-end: {timeline?.sessionEndPosition ?? 100}%;">
          <div class="scrubber-track" aria-hidden="true">
            <span class="session-segment"></span>
            {#if timeline && timeline.endMs > timeline.closeMs}
              <span class="after-close-segment"><span>장 마감 뒤</span></span>
            {/if}
          </div>
          <div class="density-strip" aria-hidden="true">
            {#each density as bucket}
              {#if bucket.count > 0}
                <span style="--position: {bucket.position}%; --intensity: {bucket.intensity};"></span>
              {/if}
            {/each}
          </div>
          <input
            type="range"
            min="0"
            max="100"
            step="0.01"
            value={playheadPosition}
            oninput={updateRange}
            aria-label="재생 시점"
            aria-valuetext={`${formatTime(playhead)}${afterClose ? ' 장 마감 뒤' : ''}`}
          />
          <div class="event-ticks">
            {#each scrubberTicks as event}
              <button
                type="button"
                tabindex="-1"
                style="--position: {eventPosition(event)}%;"
                class:past={Date.parse(event) <= playheadMs}
                onclick={() => jumpTo(event)}
                aria-label={`${formatTime(event)} 사건으로 이동`}
              ></button>
            {/each}
          </div>
        </div>
      </div>

      <div class="speed-group" aria-label="재생 속도">
        {#each [1, 4, 16] as option}
          <button type="button" class:active={speed === option} aria-pressed={speed === option} onclick={() => speed = option}>{option}x</button>
        {/each}
      </div>
    </section>
  {/if}
</div>

<style>
  .replay-page {
    --replay-chart-panel-height: 400px;
    width: 100%;
    max-width: 1320px;
    min-height: 0;
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: var(--space-3);
    overflow-y: auto;
    scrollbar-gutter: stable;
    padding-bottom: var(--space-2);
    scrollbar-color: var(--surface-raised) var(--surface-floor);
    scrollbar-width: thin;
  }

  .replay-page::-webkit-scrollbar {
    width: var(--space-2);
  }

  .replay-page::-webkit-scrollbar-track {
    background: var(--surface-floor);
  }

  .replay-page::-webkit-scrollbar-thumb {
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
  }

  .status-panel {
    margin: auto;
    min-width: min(520px, 100%);
    padding: var(--space-8);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    text-align: center;
  }

  .status-kicker,
  .section-kicker {
    font-size: 11px;
    font-weight: 700;
    color: var(--text-tertiary);
  }

  .replay-header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--space-8);
    padding: var(--space-2) var(--space-3) var(--space-1);
  }

  .header-copy {
    min-width: 0;
  }

  .header-meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-2);
    color: var(--text-tertiary);
    font-size: 12px;
    font-weight: 600;
  }

  .header-separator {
    width: 1px;
    height: var(--space-3);
    background: var(--border-strong);
  }

  .fixture-badge,
  .version-badge {
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 600;
  }

  h1 {
    margin: 0;
    max-width: 720px;
    color: var(--text-primary);
    font-size: 24px;
    line-height: 1.25;
    letter-spacing: -0.025em;
  }

  .header-copy p {
    margin: var(--space-2) 0 0;
    color: var(--text-secondary);
    font-size: 13px;
    line-height: 1.5;
  }

  .header-price {
    display: grid;
    grid-template-columns: auto auto;
    align-items: baseline;
    column-gap: var(--space-3);
    flex: 0 0 auto;
  }

  .header-price .symbol {
    grid-column: 1 / -1;
    justify-self: end;
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .header-price strong {
    font-size: 28px;
    line-height: 1;
  }

  .header-price span:last-child {
    font-size: 13px;
    font-weight: 700;
  }

  .replay-grid {
    min-height: 680px;
    display: grid;
    grid-template-columns: minmax(0, 2.1fr) minmax(320px, 1fr);
    align-items: start;
    gap: var(--space-3);
  }

  .replay-main,
  .replay-aside {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    align-self: start;
  }

  .replay-main :global(.replay-chart) {
    height: var(--replay-chart-panel-height);
    flex: 0 0 var(--replay-chart-panel-height);
  }

  .section-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
    border-bottom: 1px solid var(--border-subtle);
  }

  .section-head > div {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .section-head strong {
    color: var(--text-primary);
    font-size: 13px;
  }

  .document-count {
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .lanes {
    overflow: hidden;
    flex: 0 0 auto;
  }

  .lanes-scroll {
    overflow-x: auto;
    scrollbar-width: none;
  }

  .lanes-scroll::-webkit-scrollbar {
    display: none;
  }

  .lanes-canvas {
    position: relative;
    min-width: 720px;
    padding: var(--space-2) var(--space-3) var(--space-3);
  }

  .lanes-canvas.empty {
    min-width: 0;
  }

  .lanes-empty {
    min-height: calc(var(--space-8) + var(--space-8) + var(--space-6));
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: var(--space-1);
    color: var(--text-tertiary);
    text-align: center;
  }

  .lanes-empty strong {
    color: var(--text-secondary);
    font-size: 12px;
  }

  .lanes-empty span {
    font-size: 11px;
  }

  .lane {
    display: grid;
    grid-template-columns: 92px minmax(0, 1fr);
    align-items: stretch;
    min-height: calc(var(--space-8) + var(--space-6));
    border-bottom: 1px solid var(--border-subtle);
  }

  .lane:last-of-type {
    border-bottom: none;
  }

  .lane-label {
    display: flex;
    align-items: center;
    padding-right: var(--space-3);
    color: var(--text-tertiary);
    font-size: 11px;
    font-weight: 600;
  }

  .lane.active .lane-label {
    color: var(--text-primary);
  }

  .lane-track {
    position: relative;
    min-width: 0;
    background: color-mix(in srgb, var(--surface-floor) 45%, transparent);
  }

  .lane-track::after {
    content: '';
    position: absolute;
    z-index: 0;
    top: 0;
    right: 0;
    bottom: 0;
    left: var(--session-end);
    border-left: 1px solid var(--border-strong);
    background: color-mix(in srgb, var(--surface-raised) 56%, transparent);
    pointer-events: none;
  }

  .lane.positive.active .lane-track {
    background: color-mix(in srgb, var(--color-positive) 8%, transparent);
  }

  .lane.neutral.active .lane-track {
    background: color-mix(in srgb, var(--color-flat) 8%, transparent);
  }

  .lane.negative.active .lane-track {
    background: color-mix(in srgb, var(--color-negative) 9%, transparent);
  }

  .document-marker {
    position: absolute;
    left: var(--position);
    top: 34%;
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 0;
    transform: translateY(-50%);
    background: transparent;
    border: none;
    color: var(--text-tertiary);
    font-family: var(--font-sans);
    text-align: left;
    cursor: default;
    outline: none;
    min-width: 0;
    z-index: 1;
  }

  .premarket-group {
    position: absolute;
    z-index: 4;
    top: 50%;
    left: var(--space-1);
    transform: translateY(-50%);
  }

  .premarket-group summary {
    box-sizing: border-box;
    min-width: calc(var(--space-8) + var(--space-6));
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
    color: var(--text-secondary);
    font-size: 10px;
    font-weight: 700;
    list-style: none;
    cursor: pointer;
  }

  .premarket-group summary::-webkit-details-marker,
  .premarket-log summary::-webkit-details-marker {
    display: none;
  }

  .premarket-group summary strong {
    color: var(--text-primary);
  }

  .premarket-group[open] {
    z-index: 16;
  }

  .premarket-documents {
    position: absolute;
    top: calc(100% + var(--space-1));
    left: 0;
    width: min(320px, calc(100vw - var(--space-8) - var(--space-8)));
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
  }

  .premarket-documents div {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: var(--space-2);
    color: var(--text-secondary);
    font-size: 11px;
    line-height: 1.4;
  }

  .premarket-documents time,
  .premarket-documents p {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .premarket-documents p {
    margin: 0;
  }

  .document-marker:hover,
  .document-marker:focus,
  .document-marker:focus-visible {
    z-index: 14;
  }

  .marker-dot {
    width: var(--space-3);
    height: var(--space-3);
    flex: 0 0 var(--space-3);
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid var(--border-strong);
    background: var(--surface-raised);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 9px;
  }

  .document-marker.ranked .marker-dot {
    background: var(--text-primary);
    color: var(--surface-floor);
  }

  .document-marker.clustered .marker-dot {
    min-width: var(--space-6);
    width: auto;
    height: var(--space-6);
    padding: 0 var(--space-1);
    box-sizing: border-box;
    border-radius: var(--radius-md);
  }

  .marker-label {
    position: absolute;
    top: calc(100% + var(--space-1));
    left: 0;
    max-width: calc(var(--space-8) * 7);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 10px;
  }

  .document-tooltip {
    position: absolute;
    z-index: 12;
    top: 50%;
    left: 0;
    width: max-content;
    max-width: min(280px, calc(100vw - var(--space-8) - var(--space-8)));
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
    color: var(--text-primary);
    font-size: 11px;
    line-height: 1.45;
    white-space: normal;
    transform: translateY(-50%);
    visibility: hidden;
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--dur-hover) var(--ease-out);
  }

  .document-tooltip strong {
    color: var(--text-secondary);
    font-size: 10px;
  }

  .tooltip-documents {
    display: flex;
    max-height: calc(var(--space-8) * 8);
    flex-direction: column;
    gap: var(--space-2);
    overflow-y: auto;
  }

  .tooltip-document {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: var(--space-2);
  }

  .document-tooltip time {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .document-marker:hover .document-tooltip,
  .document-marker:focus .document-tooltip,
  .document-marker:focus-visible .document-tooltip {
    visibility: visible;
    opacity: 1;
    pointer-events: auto;
  }

  .document-marker.align-right .document-tooltip {
    right: 0;
    left: auto;
  }

  .lane-playhead {
    position: absolute;
    top: var(--space-2);
    bottom: var(--space-3);
    width: 1px;
    margin-left: 104px;
    background: var(--text-secondary);
    pointer-events: none;
  }

  .signal-card {
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .signal-head,
  .live-price {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
  }

  .signal-head time {
    color: var(--text-tertiary);
    font-size: 12px;
  }

  .signal-time {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }

  .after-close-badge {
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
    color: var(--text-secondary);
    font-size: 10px;
    font-weight: 700;
  }

  .live-price strong {
    color: var(--text-primary);
    font-size: 15px;
  }

  .live-price span {
    font-size: 18px;
    font-weight: 700;
  }

  h2 {
    margin: var(--space-1) 0 var(--space-2);
    color: var(--text-primary);
    font-size: 22px;
    line-height: 1.35;
    letter-spacing: -0.025em;
    word-break: keep-all;
  }

  .cause-list,
  .log-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .cause-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .cause-list > li {
    display: grid;
    grid-template-columns: var(--space-6) 1fr;
    gap: var(--space-2);
  }

  .cause-rank {
    width: var(--space-6);
    height: var(--space-6);
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--text-primary);
    color: var(--surface-floor);
    font-size: 11px;
    font-weight: 700;
  }

  .cause-list strong {
    font-size: 13px;
  }

  .cause-list p,
  .hidden-state p {
    margin: var(--space-1) 0;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.55;
  }

  .source-count {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .source-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    margin: var(--space-2) 0 0;
    padding: 0;
    list-style: none;
  }

  .source-list li {
    min-width: 0;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2);
    border-left: 1px solid var(--border-strong);
    background: color-mix(in srgb, var(--surface-overlay) 70%, transparent);
    font-size: 10px;
  }

  .source-list span {
    color: var(--text-tertiary);
    white-space: nowrap;
  }

  .source-list strong {
    overflow: hidden;
    color: var(--text-secondary);
    font-size: 10px;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .hidden-state {
    padding: var(--space-4);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
  }

  .hidden-state strong {
    color: var(--text-primary);
    font-size: 15px;
  }

  .signal-meta {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-2) var(--space-4);
    margin: var(--space-1) 0 0;
    padding-top: var(--space-3);
    border-top: 1px solid var(--border-subtle);
  }

  .signal-meta div {
    min-width: 0;
  }

  .signal-meta dt {
    margin-bottom: var(--space-1);
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .signal-meta dd {
    margin: 0;
    overflow: hidden;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .log-panel {
    min-height: 0;
    display: flex;
    flex: 1;
    flex-direction: column;
    overflow: hidden;
  }

  .log-head {
    align-items: flex-start;
  }

  .log-content {
    min-height: 0;
    overflow-y: auto;
    scrollbar-color: var(--surface-raised) var(--surface-body);
    scrollbar-width: thin;
  }

  .premarket-log {
    margin: var(--space-2) var(--space-4) 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  .premarket-log summary {
    display: grid;
    grid-template-columns: auto auto 1fr;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) 0;
    color: var(--text-secondary);
    font-size: 11px;
    list-style: none;
    cursor: pointer;
  }

  .premarket-log summary strong {
    color: var(--text-primary);
  }

  .summary-action {
    justify-self: end;
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .premarket-log[open] .summary-action {
    visibility: hidden;
  }

  .premarket-log-list {
    padding: 0 0 var(--space-2);
  }

  .log-list {
    min-height: 0;
    padding: var(--space-2) var(--space-4) var(--space-4);
    scrollbar-color: var(--surface-raised) var(--surface-body);
    scrollbar-width: thin;
  }

  .log-list::-webkit-scrollbar {
    width: var(--space-2);
  }

  .log-list::-webkit-scrollbar-track {
    background: var(--surface-body);
  }

  .log-list::-webkit-scrollbar-thumb {
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
  }

  .log-list li {
    display: grid;
    grid-template-columns: 42px 40px minmax(0, 1fr);
    gap: var(--space-2);
    padding: var(--space-2) 0;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-tertiary);
  }

  .log-list li.newest {
    color: var(--text-primary);
  }

  .log-list time,
  .log-kind {
    font-size: 10px;
  }

  .log-kind {
    font-weight: 700;
  }

  .log-list p {
    margin: 0;
    font-size: 11px;
    line-height: 1.45;
  }

  .empty-log {
    margin: auto;
    padding: var(--space-6);
    color: var(--text-tertiary);
    font-size: 12px;
    text-align: center;
  }

  .playback {
    position: sticky;
    bottom: 0;
    z-index: 10;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
    background: color-mix(in srgb, var(--surface-body) 96%, transparent);
  }

  .key-events {
    grid-column: 1 / -1;
    min-width: 0;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    padding-bottom: var(--space-2);
    border-bottom: 1px solid var(--border-subtle);
  }

  .key-events-title {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    white-space: nowrap;
  }

  .key-events-title strong {
    color: var(--text-secondary);
    font-size: 11px;
  }

  .key-events-title span {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .key-event-list {
    min-width: 0;
    display: flex;
    gap: var(--space-2);
    margin: 0;
    padding: 0 0 var(--space-1);
    overflow-x: auto;
    list-style: none;
    scrollbar-color: var(--surface-raised) transparent;
    scrollbar-width: thin;
  }

  .key-event-list li {
    flex: 0 0 auto;
  }

  .key-event-list button {
    max-width: calc(var(--space-8) * 10);
    min-height: var(--space-8);
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr);
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
    color: var(--text-secondary);
    font-family: var(--font-sans);
    cursor: pointer;
    transition: background var(--dur-hover) var(--ease-out), border-color var(--dur-hover) var(--ease-out);
  }

  .key-event-list button:hover,
  .key-event-list button.current {
    border-color: var(--border-strong);
    background: var(--surface-raised);
  }

  .key-event-list button.current {
    border-color: var(--brand);
  }

  .key-event-list button:focus-visible,
  .skip-toggle input:focus-visible + .toggle-track {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  .key-event-list time,
  .key-event-kind,
  .key-event-headline,
  .skip-toggle {
    font-size: 10px;
  }

  .key-event-list time {
    color: var(--text-tertiary);
  }

  .key-event-kind {
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--surface-floor);
    color: var(--text-secondary);
    font-weight: 700;
    white-space: nowrap;
  }

  .key-event-kind.positive { color: var(--color-positive); }
  .key-event-kind.negative { color: var(--color-negative); }
  .key-event-kind.neutral { color: var(--color-flat); }
  .key-event-kind.signal { color: var(--brand); }

  .key-event-headline {
    overflow: hidden;
    color: var(--text-primary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .skip-toggle {
    position: relative;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-secondary);
    cursor: pointer;
    white-space: nowrap;
  }

  .skip-toggle input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }

  .toggle-track {
    position: relative;
    width: calc(var(--space-6) + var(--space-1));
    height: var(--space-4);
    box-sizing: border-box;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-md);
    background: var(--surface-raised);
    transition: background var(--dur-hover) var(--ease-out), border-color var(--dur-hover) var(--ease-out);
  }

  .toggle-track span {
    position: absolute;
    top: 50%;
    left: var(--space-1);
    width: var(--space-2);
    height: var(--space-2);
    transform: translateY(-50%);
    border-radius: var(--radius-md);
    background: var(--text-tertiary);
    transition: transform var(--dur-hover) var(--ease-out), background var(--dur-hover) var(--ease-out);
  }

  .skip-toggle input:checked + .toggle-track {
    border-color: var(--brand);
    background: color-mix(in srgb, var(--brand) 28%, var(--surface-raised));
  }

  .skip-toggle input:checked + .toggle-track span {
    transform: translate(var(--space-3), -50%);
    background: var(--text-on-brand);
  }

  .transport,
  .speed-group {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .icon-button,
  .play-button,
  .speed-group button {
    display: grid;
    place-items: center;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--surface-overlay);
    color: var(--text-secondary);
    cursor: pointer;
    transition: background var(--dur-hover) var(--ease-out), color var(--dur-hover) var(--ease-out), border-color var(--dur-hover) var(--ease-out);
  }

  .icon-button,
  .play-button {
    width: var(--space-8);
    height: var(--space-8);
    padding: 0;
  }

  .play-button {
    background: var(--brand);
    border-color: var(--brand);
    color: var(--text-on-brand);
  }

  .icon-button:hover,
  .speed-group button:hover,
  .speed-group button.active {
    background: var(--surface-raised);
    border-color: var(--border-strong);
    color: var(--text-primary);
  }

  .icon-button:focus-visible,
  .play-button:focus-visible,
  .speed-group button:focus-visible,
  .document-marker:focus-visible,
  input[type='range']:focus-visible {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  .icon-button svg,
  .play-button svg {
    width: var(--space-4);
    height: var(--space-4);
    fill: currentColor;
  }

  .scrubber-wrap {
    min-width: 0;
  }

  .scrubber-meta {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: var(--space-3);
    margin-bottom: var(--space-1);
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .scrubber-meta span {
    overflow: hidden;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .scrubber-meta span.fast-forwarding {
    color: var(--text-secondary);
  }

  .scrubber {
    position: relative;
    height: var(--space-6);
  }

  .scrubber-track {
    position: absolute;
    z-index: 0;
    top: var(--space-2);
    right: 0;
    left: 0;
    height: var(--space-2);
    overflow: hidden;
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
  }

  .session-segment,
  .after-close-segment {
    position: absolute;
    top: 0;
    bottom: 0;
  }

  .session-segment {
    left: 0;
    width: var(--session-end);
    background: var(--surface-raised);
  }

  .after-close-segment {
    right: 0;
    left: var(--session-end);
    border-left: 1px solid var(--border-strong);
    background: color-mix(in srgb, var(--text-tertiary) 24%, var(--surface-floor));
  }

  .after-close-segment span {
    position: absolute;
    right: var(--space-1);
    bottom: calc(100% + var(--space-1));
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 700;
    white-space: nowrap;
  }

  .density-strip {
    position: absolute;
    z-index: 1;
    right: 0;
    bottom: 0;
    left: 0;
    height: var(--space-1);
    overflow: hidden;
    pointer-events: none;
  }

  .density-strip span {
    position: absolute;
    left: var(--position);
    bottom: 0;
    width: var(--space-1);
    height: var(--space-1);
    transform: translateX(-50%);
    border-radius: var(--radius-sm);
    background: var(--text-tertiary);
    opacity: calc(0.12 + var(--intensity) * 0.42);
  }

  input[type='range'] {
    position: absolute;
    z-index: 2;
    inset: 0;
    width: 100%;
    height: var(--space-6);
    margin: 0;
    appearance: none;
    background: transparent;
    cursor: pointer;
  }

  input[type='range']::-webkit-slider-runnable-track {
    height: var(--space-2);
    background: transparent;
  }

  input[type='range']::-webkit-slider-thumb {
    width: var(--space-4);
    height: var(--space-4);
    margin-top: calc(var(--space-1) * -1);
    appearance: none;
    border: 1px solid var(--text-on-brand);
    border-radius: 50%;
    background: var(--brand);
  }

  input[type='range']::-moz-range-track {
    height: var(--space-2);
    background: transparent;
  }

  input[type='range']::-moz-range-thumb {
    width: var(--space-4);
    height: var(--space-4);
    border: 1px solid var(--text-on-brand);
    border-radius: 50%;
    background: var(--brand);
  }

  .event-ticks {
    position: absolute;
    z-index: 3;
    inset: 0;
    pointer-events: none;
  }

  .event-ticks button {
    position: absolute;
    left: var(--position);
    top: var(--space-3);
    width: var(--space-4);
    height: var(--space-6);
    padding: 0;
    transform: translate(-50%, -50%);
    border: none;
    background: transparent;
    pointer-events: auto;
    cursor: pointer;
  }

  .event-ticks button::before {
    content: '';
    position: absolute;
    top: var(--space-2);
    left: 50%;
    width: var(--space-1);
    height: var(--space-2);
    transform: translate(-50%, -50%);
    border-radius: var(--radius-sm);
    background: var(--text-tertiary);
  }

  .event-ticks button.past::before {
    background: var(--text-primary);
  }

  .speed-group button {
    min-width: var(--space-8);
    padding: var(--space-1) var(--space-2);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  @media (max-width: 900px) {
    .replay-grid {
      min-height: auto;
      grid-template-columns: 1fr;
    }

    .replay-aside {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .log-panel {
      min-height: 360px;
    }
  }

  @media (max-width: 600px) {
    .replay-page {
      gap: var(--space-2);
      padding-bottom: calc(var(--space-8) + var(--space-8) + var(--space-8) + var(--space-6));
    }

    .replay-header {
      align-items: flex-start;
      flex-direction: column;
      gap: var(--space-3);
      padding-inline: var(--space-1);
    }

    h1 {
      font-size: 20px;
    }

    .header-copy p {
      font-size: 12px;
    }

    .header-price {
      width: 100%;
    }

    .header-price .symbol {
      justify-self: start;
    }

    .header-price strong {
      font-size: 24px;
    }

    .replay-aside {
      display: flex;
    }

    .section-head {
      align-items: flex-start;
      flex-direction: column;
      gap: var(--space-2);
    }

    .signal-meta {
      grid-template-columns: 1fr;
    }

    .playback {
      position: fixed;
      right: var(--space-3);
      bottom: var(--space-3);
      left: var(--space-3);
      grid-template-columns: 1fr auto;
      gap: var(--space-2);
      padding: var(--space-3);
    }

    .key-events {
      grid-row: 1;
      grid-template-columns: minmax(0, 1fr) auto;
    }

    .key-events-title {
      display: none;
    }

    .skip-toggle > span:last-child {
      display: none;
    }

    .scrubber-wrap {
      grid-column: 1 / -1;
      grid-row: 2;
    }

    .transport {
      grid-column: 1;
      grid-row: 3;
    }

    .speed-group {
      grid-column: 2;
      grid-row: 3;
    }

    .scrubber-meta span {
      display: none;
    }

    .scrubber-meta {
      grid-template-columns: auto 1fr;
    }

    .scrubber-meta time:last-child {
      justify-self: end;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .icon-button,
    .play-button,
    .speed-group button {
      transition: none;
    }

    .key-event-list button,
    .toggle-track,
    .toggle-track span {
      transition: none;
    }
  }
</style>
