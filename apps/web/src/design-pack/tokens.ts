/**
 * Transliate — Design tokens (Light mode)
 * Use for charts (Recharts…), inline styles, canvas/PDF rendering, emails.
 */

export const colors = {
  // Surfaces
  bg:      '#ebeff5',
  bgAlt:   '#f4f6fa',
  card:    '#ffffff',
  pageGradient: 'linear-gradient(90deg, #e8effb 0%, #f8fafc 38%, #f1f5f9 100%)',

  // Borders
  border:   '#c5ccd6',
  borderLt: '#dde2ea',
  borderUi: '#e2e8f0',

  // Text
  text:      '#000000',
  text2:     '#0f172a',
  textMuted: '#1e293b',
  textDim:   '#334155',
  textSoft:  '#64748b',
  textFaint: '#94a3b8',
  placeholder: '#475569',

  // Actions
  primary:      '#0f172a',
  primaryHover: '#1e293b',
  cta:          '#E8820C',
  ctaHover:     '#c96a00',
  focusRing:    '#1A56DB',

  // Brand
  brand: {
    50:  '#EFF6FF',
    100: '#DBEAFE',
    200: '#BFDBFE',
    500: '#3B82F6',
    600: '#1A56DB',
    700: '#1040B0',
    900: '#1E3A5F',
  },
  logoNavy: '#0A2540',

  // Semantic
  success: '#059669',
  warning: '#d97706',
  danger:  '#dc2626',
  info:    '#3b82f6',

  // Table
  tblHeader: '#eef2f7',
  tblHover:  'rgba(10,22,40,0.025)',
  overlay:   'rgba(10,22,40,0.45)',
} as const;

export const badgeColors = {
  blue:   { bg: '#eff6ff', fg: '#1d4ed8' },
  green:  { bg: '#ecfdf5', fg: '#047857' },
  amber:  { bg: '#fffbeb', fg: '#b45309' },
  red:    { bg: '#fef2f2', fg: '#b91c1c' },
  gray:   { bg: '#f1f5f9', fg: '#475569' },
  purple: { bg: '#faf5ff', fg: '#7e22ce' },
} as const;

export const typography = {
  fontSans: "'DM Sans', system-ui, sans-serif",
  fontMono: "'DM Mono', 'Fira Code', monospace",
  weights:  { regular: 400, medium: 500, semibold: 600, bold: 700, extrabold: 800 },
  sizes: {
    micro: 10, xs: 12, cta: 13, sm: 14, base: 16, lg: 18,
    xl: 20, '2xl': 24, '3xl': 30, '4xl': 36, '5xl': 48, '6xl': 60,
  },
  tracking: { tight: '-0.025em', wide: '0.025em', label: '0.14em', eyebrow: '0.18em' },
} as const;

export const radius = {
  btn: 8, card: 12, xl: 12, '2xl': 16, modal: 16, panel: 28, pill: 9999,
} as const;

export const shadows = {
  sm:    '0 1px 2px 0 rgb(0 0 0 / 0.05)',
  card:  '0 1px 3px 0 rgb(0 0 0 / 0.05), 0 1px 2px -1px rgb(0 0 0 / 0.05)',
  panel: '0 20px 25px -5px rgba(226,232,240,0.4), 0 8px 10px -6px rgba(226,232,240,0.4)',
  modal: '0 20px 60px -10px rgb(0 0 0 / 0.12)',
} as const;

export const motion = {
  fast: '150ms', base: '200ms', slow: '300ms',
  ease: 'cubic-bezier(0.4, 0, 0.2, 1)',
  pressScale: 0.98,
} as const;

/** Chart series order — as used in admin/ProfitabilityPage (Recharts) */
export const chartPalette = ['#0f172a', '#E8820C', '#059669', '#6366f1', '#ec4899', '#8b5cf6'] as const;

export const tokens = { colors, badgeColors, typography, radius, shadows, motion, chartPalette };
export default tokens;
