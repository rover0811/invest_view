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
