/**
 * Transliate — Tailwind preset (Light mode) · Tailwind CSS v3
 *
 * tailwind.config.js:
 *   import transliate from './design-pack/tailwind-preset.js';
 *   export default { presets: [transliate], content: ['./index.html', './src/**\/*.{ts,tsx}'] };
 *
 * Component classes (.btn, .card, .input, .badge, .tbl …) come from transliate.css —
 * import it at the very top of your main CSS, before @tailwind directives.
 */

/** @type {import('tailwindcss').Config} */
export default {
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#EFF6FF',
          100: '#DBEAFE',
          200: '#BFDBFE',
          500: '#3B82F6',
          600: '#1A56DB',
          700: '#1040B0',
          900: '#1E3A5F',
        },
        cta: {
          DEFAULT: '#E8820C',
          hover:   '#c96a00',
        },
        'logo-navy': '#0A2540',
        surface: 'var(--bg-alt)',
        border:  'var(--border)',
      },
      fontFamily: {
        sans: ['DM Sans', 'system-ui', 'sans-serif'],
        mono: ['DM Mono', 'Fira Code', 'monospace'],
      },
      borderRadius: {
        card:  '12px',
        btn:   '8px',
        panel: '28px',
      },
      boxShadow: {
        card:  '0 1px 3px 0 rgb(0 0 0 / 0.05), 0 1px 2px -1px rgb(0 0 0 / 0.05)',
        modal: '0 20px 60px -10px rgb(0 0 0 / 0.12)',
        panel: '0 20px 25px -5px rgba(226,232,240,0.4), 0 8px 10px -6px rgba(226,232,240,0.4)',
      },
      letterSpacing: {
        label:   '0.14em',
        eyebrow: '0.18em',
      },
      animation: {
        'fade-in':  'fadeIn 0.15s ease-out',
        'slide-up': 'slideUp 0.2s ease-out',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
    },
  },
};
