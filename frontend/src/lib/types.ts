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

export interface Indicators {
  per: number;
  pbr: number;
  psr: number;
  eps: number;
  bps: number;
  roe: number;
  dividend_yield: number;
}

export interface Fundamentals {
  stock_name: string;
  market: string;
  industry_name: string;
  market_cap: number;
  ceo_name: string;
  listing_date: string;
  balance_sheet_summary: string;
  income_statement_summary: string;
  cash_flow_summary: string;
}

export interface Consensus {
  report_date: string;
  provider: string;
  title: string;
  target_price: number;
  investment_opinion: 'Buy' | 'Hold' | 'Sell';
  summary: string;
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
  tickDetail: TickDetail;
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

export interface IndexItem {
  name: string;
  value: number;
  change_rate: number;
  sparkline: number[];
}
