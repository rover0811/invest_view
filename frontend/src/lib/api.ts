import type {
  StockData,
  StockListItem,
  Candle,
  Snapshot,
  TimelineEvent,
  TickDetail,
  Consensus,
  Indicators,
  Financials,
  Fundamentals,
  ChartSpec,
} from './types';

const BASE = '/api';

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// tick-detail returns 404 when no tick row exists for the symbol (e.g. market
// closed / never traded). That is an expected empty state, not an error.
async function getTickDetail(symbol: string): Promise<TickDetail | null> {
  const res = await fetch(`${BASE}/tick-detail/${symbol}`);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`GET /tick-detail/${symbol} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<TickDetail>;
}

interface StockInfoResponse {
  meta: {
    stock_name: string;
    market: string;
    industry_name: string;
    market_cap: number | null;
    ceo_name: string | null;
    listing_date: string | null;
  };
  financials: Financials;
  indicators: Indicators;
  coverage_note: string;
}

export async function getStockData(symbol: string): Promise<StockData> {
  const [candles, snapshot, timeline, tickDetail, stockInfo, consensus] = await Promise.all([
    getJson<Candle[]>(`/candles/${symbol}`),
    getJson<Snapshot>(`/snapshot/${symbol}`),
    getJson<TimelineEvent[]>(`/timeline/${symbol}`),
    getTickDetail(symbol),
    getJson<StockInfoResponse>(`/stock-info/${symbol}?period_type=Y`),
    getJson<Consensus[]>(`/consensus/${symbol}`),
  ]);

  const fundamentals: Fundamentals = {
    stock_name: stockInfo.meta.stock_name,
    market: stockInfo.meta.market,
    industry_name: stockInfo.meta.industry_name,
    market_cap: stockInfo.meta.market_cap,
    ceo_name: stockInfo.meta.ceo_name,
    listing_date: stockInfo.meta.listing_date,
    financials: stockInfo.financials,
    coverage_note: stockInfo.coverage_note,
  };

  return {
    _meta: {
      symbol,
      stock_name: stockInfo.meta.stock_name ?? snapshot.symbol,
    },
    candles,
    snapshot,
    timeline,
    tickDetail,
    fundamentals,
    consensus,
    indicators: stockInfo.indicators,
  };
}

export type CandleInterval = '5m' | '1d' | '1w' | '1M';

export async function getCandles(symbol: string, interval: CandleInterval = '5m'): Promise<Candle[]> {
  const query = interval === '5m' ? '' : `?interval=${interval}`;
  return getJson<Candle[]>(`/candles/${symbol}${query}`);
}

export async function getStockList(): Promise<StockListItem[]> {
  return getJson<StockListItem[]>('/stocks');
}

// Dev seam: VITE_DEV_JWT (build-time) takes precedence over a localStorage
// token, since there is no login UI yet to populate localStorage.
function getAuthToken(): string | null {
  const fromEnv = import.meta.env.VITE_DEV_JWT;
  if (fromEnv) return fromEnv;
  if (typeof window !== 'undefined') {
    return window.localStorage.getItem('authToken');
  }
  return null;
}

export function hasAuthToken(): boolean {
  return getAuthToken() !== null;
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getAuthToken();
  const headers: Record<string, string> = { ...extra };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export interface AgentSession {
  session_id: string;
  ticker: string;
}

export async function createAgentSession(ticker: string): Promise<AgentSession> {
  const res = await fetch(`${BASE}/agent/sessions`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ ticker }),
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('인증이 필요합니다');
    throw new Error(`POST /agent/sessions failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<AgentSession>;
}

export interface AgentStreamCallbacks {
  onToken(text: string): void;
  onDone(info: { message_id: string; status: string; sibling_count?: number }): void;
  onError(message: string): void;
  onChart?(spec: ChartSpec): void;
}

function dispatchFrame(frame: string, callbacks: AgentStreamCallbacks): void {
  let event = 'message';
  const dataLines: string[] = [];
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    // SSE spec: a single leading space after the field colon is stripped.
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (dataLines.length === 0) return;

  const dataStr = dataLines.join('\n');
  let payload: unknown;
  try {
    payload = JSON.parse(dataStr);
  } catch {
    return;
  }
  const p = payload as Record<string, unknown>;
  if (event === 'token') {
    if (typeof p.text === 'string') callbacks.onToken(p.text);
  } else if (event === 'done') {
    callbacks.onDone({
      message_id: String(p.message_id ?? ''),
      status: String(p.status ?? ''),
      sibling_count: typeof p.sibling_count === 'number' ? p.sibling_count : undefined,
    });
  } else if (event === 'error') {
    callbacks.onError(typeof p.message === 'string' ? p.message : '스트리밍 오류가 발생했습니다');
  } else if (event === 'chart') {
    if (isChartSpec(p.spec)) callbacks.onChart?.(p.spec);
  }
}

function isChartSpec(v: unknown): v is ChartSpec {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  if (o.chart_type !== 'line' && o.chart_type !== 'bar') return false;
  if (
    typeof o.title !== 'string' ||
    typeof o.x_label !== 'string' ||
    typeof o.y_label !== 'string' ||
    typeof o.unit !== 'string'
  ) {
    return false;
  }
  if (!Array.isArray(o.series)) return false;
  for (const s of o.series) {
    if (!s || typeof s !== 'object') return false;
    const series = s as Record<string, unknown>;
    if (typeof series.name !== 'string' || !Array.isArray(series.points)) return false;
    for (const point of series.points) {
      if (!point || typeof point !== 'object') return false;
      const pt = point as Record<string, unknown>;
      if (typeof pt.x !== 'string' || typeof pt.y !== 'number' || !Number.isFinite(pt.y)) {
        return false;
      }
    }
  }
  return true;
}

// A token frame can be split across network chunks, so frames are accumulated
// in a buffer and only dispatched once terminated by a blank line ("\n\n").
async function consumeSSE(
  body: ReadableStream<Uint8Array>,
  callbacks: AgentStreamCallbacks,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const drainCompleteFrames = () => {
    buffer = buffer.replace(/\r\n/g, '\n');
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (frame.trim() !== '') dispatchFrame(frame, callbacks);
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    drainCompleteFrames();
  }
  buffer += decoder.decode();
  drainCompleteFrames();
  const tail = buffer.trim();
  if (tail !== '') dispatchFrame(tail, callbacks);
}

async function openAgentStream(
  path: string,
  body: Record<string, unknown> | null,
  callbacks: AgentStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: authHeaders({
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      }),
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    // An abort is an intentional stop, not an error — partial text is kept.
    if ((err as Error)?.name === 'AbortError') return;
    callbacks.onError('네트워크 오류가 발생했습니다');
    return;
  }

  if (!res.ok) {
    if (res.status === 401) callbacks.onError('인증이 필요합니다');
    else callbacks.onError(`요청 실패: ${res.status} ${res.statusText}`);
    return;
  }
  if (!res.body) {
    callbacks.onError('스트림 응답이 비어 있습니다');
    return;
  }

  try {
    await consumeSSE(res.body, callbacks);
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return;
    callbacks.onError('스트림을 읽는 중 오류가 발생했습니다');
  }
}

export async function streamAgentChat(
  sessionId: string,
  text: string,
  callbacks: AgentStreamCallbacks,
  opts?: { parentId?: string | null; signal?: AbortSignal },
): Promise<void> {
  return openAgentStream(
    `/agent/sessions/${sessionId}/stream`,
    { text, parent_id: opts?.parentId ?? null },
    callbacks,
    opts?.signal,
  );
}

export async function regenerateAgentChat(
  sessionId: string,
  messageId: string,
  callbacks: AgentStreamCallbacks,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  return openAgentStream(
    `/agent/sessions/${sessionId}/messages/${messageId}/regenerate`,
    null,
    callbacks,
    opts?.signal,
  );
}
