export type Route = {
  view: 'home' | 'stock' | 'replay';
  code?: string;
  tab?: string;
  section?: string;
  symbol?: string;
  date?: string;
};

export const appState = $state<{
  route: Route;
  currentSymbol: string;
  activeTab: string;
}>({
  route: { view: 'home' },
  currentSymbol: '005930',
  activeTab: 'chart'
});

let routerInitialized = false;

function homeRoute(): Route {
  return { view: 'home' };
}

function isValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function parseHash(): Route {
  if (typeof window === 'undefined') {
    return homeRoute();
  }

  const hash = window.location.hash.trim();
  if (hash === '' || hash === '#' || hash === '#/') {
    return homeRoute();
  }

  const hashPath = hash.startsWith('#') ? hash.slice(1) : hash;

  try {
    const url = new URL(hashPath.startsWith('/') ? hashPath : `/${hashPath}`, window.location.origin);

    if (url.pathname === '/') {
      return homeRoute();
    }

    const replayMatch = /^\/replay\/([^/]+)\/(\d{4}-\d{2}-\d{2})$/.exec(url.pathname);
    if (replayMatch) {
      const symbol = decodeURIComponent(replayMatch[1]).trim();
      const date = replayMatch[2];
      if (/^\d{6}$/.test(symbol) && isValidDate(date)) {
        return { view: 'replay', symbol, date };
      }
      return homeRoute();
    }

    const stockMatch = /^\/stocks\/([^/]+)$/.exec(url.pathname);
    if (!stockMatch) {
      return homeRoute();
    }

    const code = decodeURIComponent(stockMatch[1]).trim();
    if (code === '') {
      return homeRoute();
    }

    const tab = url.searchParams.get('tab')?.trim() || 'chart';
    const section = url.searchParams.get('section')?.trim() || undefined;
    return { view: 'stock', code, tab, section };
  } catch {
    return homeRoute();
  }
}

export function navigate(path: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.location.hash = path;
}

export function initRouter(): void {
  if (typeof window === 'undefined' || routerInitialized) {
    return;
  }

  routerInitialized = true;

  if (window.location.hash === '') {
    window.location.hash = '#/';
  }

  appState.route = parseHash();

  window.addEventListener('hashchange', () => {
    appState.route = parseHash();
  });
}
