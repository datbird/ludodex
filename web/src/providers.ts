// Centralized provider → color. The single source of truth for any place we
// visually attribute something to a provider: attribute-value badges, source
// rows, adjudication origins, tags. Add a provider here and it colors everywhere.
export const PROVIDER_COLORS: Record<string, string> = {
  // metadata providers
  igdb: '#8b5cf6',           // violet
  screenscraper: '#f59e0b',  // amber
  steamgriddb: '#5eb3ef',    // the accent band of its own logo
  thegamesdb: '#7ab648',     // the green of its own wordmark
  // stores
  steam: '#3ba7e0',
  gog: '#a855f7',
  epic: '#9aa0a6',
  xbox: '#107c10',
  psn: '#0070d1',
  ea: '#ff5252',
  itch: '#fa5c5c',
  ubisoft: '#1f9fe0',
  battlenet: '#00aeff',
  // meta-layers / catalog
  playnite: '#c084fc',
  launchbox: '#f0a020',
  emulation: '#2dd4bf',      // teal
  archive: '#5eead4',
  // ownership / origin markers
  ai: '#c084fc',
  // A distinct colour on purpose: 'AI knew this' and 'AI went and read this' are
  // different strengths of claim, and one badge for both hides which is which.
  ai_web: '#38bdf8',
  manual: '#94a3b8',
  import: '#94a3b8',
  physical: '#d4a95e',       // disc gold
  rom: '#2dd4bf',
  digital: '#3ba7e0',
}
const FALLBACK = '#94a3b8'

export function providerColor(id: string): string {
  return PROVIDER_COLORS[(id || '').toLowerCase()] ?? FALLBACK
}

const LABELS: Record<string, string> = {
  igdb: 'IGDB', screenscraper: 'ScreenScraper', gog: 'GOG', psn: 'PSN',
  ai_web: 'AI Web Search',
  ea: 'EA', ai: 'AI', xbox: 'Xbox', itch: 'itch.io',
  // steamgrid is the LOCAL Steam grid folder, a different provider from SteamGridDB —
  // 'Steamgrid' beside 'SteamGridDB' read as the same thing.
  steamgriddb: 'SteamGridDB', steamgrid: 'Steam grid',
  thegamesdb: 'TheGamesDB',
}
export function providerLabel(id: string): string {
  const k = (id || '').toLowerCase()
  return LABELS[k] ?? (k ? k.charAt(0).toUpperCase() + k.slice(1) : id)
}

// Short 1–2 char monogram for a favicon-style brand badge (self-contained — no
// external logo assets / favicon services). Colored via providerColor().
const MARKS: Record<string, string> = {
  igdb: 'IG', screenscraper: 'SS', steam: 'S', gog: 'GG', epic: 'E', xbox: 'X',
  psn: 'PS', ea: 'EA', itch: 'i', ubisoft: 'U', battlenet: 'B',
  steamgriddb: 'SG', steamgrid: 'SGr', thegamesdb: 'TG', ai_web: 'AIW',
}
export function providerMark(id: string): string {
  const k = (id || '').toLowerCase()
  return MARKS[k] ?? (k ? k.charAt(0).toUpperCase() : '?')
}
