// Centralized provider → color. The single source of truth for any place we
// visually attribute something to a provider: attribute-value badges, source
// rows, adjudication origins, tags. Add a provider here and it colors everywhere.
export const PROVIDER_COLORS: Record<string, string> = {
  // metadata providers
  igdb: '#8b5cf6',           // violet
  screenscraper: '#f59e0b',  // amber
  // stores
  steam: '#3ba7e0',
  gog: '#a855f7',
  epic: '#9aa0a6',
  xbox: '#107c10',
  psn: '#0070d1',
  nintendo: '#e60012',
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
  ea: 'EA', ai: 'AI', xbox: 'Xbox', itch: 'itch.io',
}
export function providerLabel(id: string): string {
  const k = (id || '').toLowerCase()
  return LABELS[k] ?? (k ? k.charAt(0).toUpperCase() + k.slice(1) : id)
}
