export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface Snapshot {
  symbol: string;
  last_price: number;
  change: number;
  change_rate: number;
  change_sign: string;
  cumulative_volume: number;
  vi_trigger_price: number;
  trading_halted: string;
  updated_at: string;
}

// /api/price always 200. price/change/etc. are null on daily-close fallback
// (source='daily_close') or no data (source='none'); is_realtime gates the
// realtime-only fields (change/체결강도/VI) from being shown as live.
export interface ResolvedPrice {
  symbol: string | null;
  price: number | null;
  source: 'realtime_snapshot' | 'daily_close' | 'none';
  as_of: string | null;
  is_realtime: boolean;
  is_stale: boolean;
  display_label: '실시간' | '장마감 종가 기준' | '데이터 없음';
  change: number | null;
  change_rate: number | null;
  change_sign: string | null;
  cumulative_volume: number | null;
  vi_trigger_price: number | null;
  trading_halted: string | null;
}

export interface TimelineEvent {
  time: number;
  event_kind: 'alert' | 'pattern';
  event_type: string;
  triggered_at: string;
  trigger_values: Record<string, string>;
}

export interface TickDetail {
  trade_strength: number;
  buy_ratio: number;
  net_buy_count: number;
  buy_count: number;
  sell_count: number;
  total_buy_volume: number;
  total_sell_volume: number;
  ask_remain_1: number;
  bid_remain_1: number;
  total_ask_remain: number;
  total_bid_remain: number;
  volume_turnover: number;
  vwap: number;
  prev_day_volume_rate: number;
}

// Backend (/api/stock-info) provides only eps/per/pbr; psr/bps/roe/dividend_yield
// are not computed. Any value is null when the source financial_metrics rows are missing.
export interface Indicators {
  eps: number | null;
  per: number | null;
  pbr: number | null;
}

export interface FinancialLine {
  item: string;
  period: string;
  value: number | null;
  unit: string | null;
}

export interface Financials {
  income: FinancialLine[];
  balance: FinancialLine[];
  cashflow: FinancialLine[];
}

// /api/stock-info `meta` + `financials`. No summary-text fields (the mock's
// *_summary fields do not exist in the backend).
export interface Fundamentals {
  stock_name: string;
  market: string;
  industry_name: string;
  market_cap: number | null;
  ceo_name: string | null;
  listing_date: string | null;
  financials: Financials;
  coverage_note: string;
}

export interface Consensus {
  report_date: string;
  provider: string;
  title: string;
  target_price: number;
  investment_opinion: 'Buy' | 'Hold' | 'Sell';
  author: string | null;
}

export interface StockData {
  _meta: {
    symbol: string;
    stock_name: string;
    [k: string]: unknown;
  };
  candles: Candle[];
  snapshot: Snapshot;
  timeline: TimelineEvent[];
  tickDetail: TickDetail | null;
  fundamentals: Fundamentals;
  consensus: Consensus[];
  indicators: Indicators;
}

export interface StockListItem {
  code: string;
  name: string;
  price: number;
  change_rate: number;
  market: string;
}

export interface ChartPoint {
  x: string;   // period e.g. "2024-12"
  y: number;
}

export interface ChartSeries {
  name: string;
  points: ChartPoint[];
}

export interface ChartSpec {
  chart_type: 'line' | 'bar';
  title: string;
  x_label: string;
  y_label: string;
  unit: string;
  series: ChartSeries[];
}

export type MessagePart =
  | { kind: 'text'; text: string }
  | { kind: 'chart'; spec: ChartSpec };
