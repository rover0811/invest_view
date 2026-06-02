import type { StockData, StockListItem, IndexItem } from './types';

const BASE = '/mock'; // This becomes '/api' for real backend later

/**
 * Fetches stock data for a given symbol.
 * Currently uses mock data and ignores the symbol parameter.
 */
export async function getStockData(symbol: string): Promise<StockData> {
  // symbol param kept in signature for future real API /api/.../${symbol}, currently unused
  const res = await fetch(`${BASE}/mock-data.json`);
  if (!res.ok) {
    throw new Error(`Failed to fetch stock data: ${res.statusText}`);
  }
  return res.json();
}

/**
 * Fetches the list of stocks.
 */
export async function getStockList(): Promise<StockListItem[]> {
  const res = await fetch(`${BASE}/stocks.json`);
  if (!res.ok) {
    throw new Error(`Failed to fetch stock list: ${res.statusText}`);
  }
  return res.json();
}

/**
 * Fetches the list of indices.
 */
export async function getIndices(): Promise<IndexItem[]> {
  const res = await fetch(`${BASE}/indices.json`);
  if (!res.ok) {
    throw new Error(`Failed to fetch indices: ${res.statusText}`);
  }
  return res.json();
}
