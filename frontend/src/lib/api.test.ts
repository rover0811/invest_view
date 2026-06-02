import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getStockData, getStockList, getCandles } from './api';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body
  } as Response;
}

const STOCK_INFO = {
  meta: {
    stock_name: '삼성전자',
    market: 'KOSPI',
    market_cap: 2259171911327000,
    industry_name: '반도체와반도체장비',
    ceo_name: '전영현, 노태문',
    listing_date: '1989-09-25'
  },
  financials: {
    income: [{ item: '매출액', period: '2025-12', value: 333605938000, unit: '천원' }],
    balance: [],
    cashflow: []
  },
  indicators: { eps: 6605, per: 48.75, pbr: 0.005 },
  coverage_note: ''
};

function routeFetch(overrides: Record<string, Response> = {}) {
  return vi.fn(async (url: string) => {
    const path = url.replace('/api', '');
    if (path in overrides) return overrides[path];
    if (path.startsWith('/candles/')) return jsonResponse([{ time: 1, open: 1, high: 2, low: 0, close: 1 }]);
    if (path.startsWith('/snapshot/')) return jsonResponse({ symbol: '005930', last_price: 322500, change: 5500, change_rate: 1.74, change_sign: '2', cumulative_volume: 1, vi_trigger_price: 1, trading_halted: 'N', updated_at: 'x' });
    if (path.startsWith('/timeline/')) return jsonResponse([{ time: 1, event_kind: 'alert', event_type: 'PRICE_ALERT', triggered_at: 'x', trigger_values: {} }]);
    if (path.startsWith('/tick-detail/')) return jsonResponse({ trade_strength: 118.3, buy_ratio: 0.62, net_buy_count: 1, buy_count: 1, sell_count: 1, total_buy_volume: 1, total_sell_volume: 1, ask_remain_1: 1, bid_remain_1: 1, total_ask_remain: 1, total_bid_remain: 1, volume_turnover: 1, vwap: 1, prev_day_volume_rate: 1 });
    if (path.startsWith('/stock-info/')) return jsonResponse(STOCK_INFO);
    if (path.startsWith('/consensus/')) return jsonResponse([{ report_date: '2026-05-30', provider: 'iM증권', title: 't', target_price: 400000, investment_opinion: 'Buy', author: '홍길동' }]);
    if (path === '/stocks') return jsonResponse([{ code: '005930', name: '삼성전자', market: 'KOSPI', price: 322500, change_rate: 1.74 }]);
    throw new Error('unexpected ' + path);
  });
}

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('getStockData', () => {
  it('assembles the 6 endpoints into StockData', async () => {
    vi.stubGlobal('fetch', routeFetch());
    const d = await getStockData('005930');
    expect(d.candles).toHaveLength(1);
    expect(d.snapshot.last_price).toBe(322500);
    expect(d.indicators).toEqual({ eps: 6605, per: 48.75, pbr: 0.005 });
    expect(d.fundamentals.stock_name).toBe('삼성전자');
    expect(d.fundamentals.financials.income).toHaveLength(1);
    expect(d.fundamentals.coverage_note).toBe('');
    expect(d.consensus[0].author).toBe('홍길동');
    expect(d._meta.symbol).toBe('005930');
    expect(d._meta.stock_name).toBe('삼성전자');
  });

  it('requests stock-info with period_type=Y (real data only has annual rows)', async () => {
    const f = routeFetch();
    vi.stubGlobal('fetch', f);
    await getStockData('005930');
    const calls = f.mock.calls.map((c) => c[0] as string);
    expect(calls.some((u) => u.includes('/stock-info/005930?period_type=Y'))).toBe(true);
  });

  it('gracefully returns tickDetail=null on 404 without failing the bundle', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/tick-detail/005930': jsonResponse({ detail: 'not found' }, 404) }));
    const d = await getStockData('005930');
    expect(d.tickDetail).toBeNull();
    expect(d.candles).toHaveLength(1);
  });

  it('throws when a required endpoint (snapshot) fails', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/snapshot/005930': jsonResponse({ detail: 'boom' }, 500) }));
    await expect(getStockData('005930')).rejects.toThrow();
  });
});

describe('getStockList', () => {
  it('returns the stock list from /api/stocks', async () => {
    vi.stubGlobal('fetch', routeFetch());
    const list = await getStockList();
    expect(list).toHaveLength(1);
    expect(list[0].code).toBe('005930');
  });
});

describe('getCandles', () => {
  it('omits the interval query for the default 5m', async () => {
    const f = routeFetch();
    vi.stubGlobal('fetch', f);
    await getCandles('005930');
    const url = f.mock.calls[0][0] as string;
    expect(url).toBe('/api/candles/005930');
  });

  it('passes interval as a query param for aggregated intervals', async () => {
    const f = routeFetch();
    vi.stubGlobal('fetch', f);
    await getCandles('005930', '1d');
    const url = f.mock.calls[0][0] as string;
    expect(url).toBe('/api/candles/005930?interval=1d');
  });
});
