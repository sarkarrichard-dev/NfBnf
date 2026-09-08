/** 16px stroke icons for the sidebar. One consistent set, `currentColor`. */
const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  className: 'h-full w-full',
}

export const IconGrid = () => (
  <svg {...base}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></svg>
)
export const IconLayers = () => (
  <svg {...base}><path d="M12 3 3 8l9 5 9-5-9-5Z" /><path d="M3 13l9 5 9-5" /><path d="M3 18l9 5 9-5" /></svg>
)
export const IconCoin = () => (
  <svg {...base}><ellipse cx="12" cy="6.5" rx="8" ry="3.5" /><path d="M4 6.5v11c0 1.9 3.6 3.5 8 3.5s8-1.6 8-3.5v-11" /><path d="M4 12c0 1.9 3.6 3.5 8 3.5s8-1.6 8-3.5" /></svg>
)
export const IconFlask = () => (
  <svg {...base}><path d="M9 3h6" /><path d="M10 3v6L4.5 18a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3" /><path d="M7 15h10" /></svg>
)
export const IconGear = () => (
  <svg {...base}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" /></svg>
)
export const IconChart = () => (
  <svg {...base}><path d="M3 3v18h18" /><path d="m7 14 3-4 4 3 5-7" /></svg>
)
export const IconMenu = () => (
  <svg {...base}><path d="M4 6h16M4 12h16M4 18h16" /></svg>
)
