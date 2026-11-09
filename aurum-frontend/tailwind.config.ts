import type { Config } from 'tailwindcss';

/**
 * AURUM AI design tokens — light "private-bank" layout (white sidebar, light grey
 * canvas, white rounded cards, one deep-green accent with dark-green feature cards).
 * Every colour in the UI should come from here.
 *
 * Note: the accent keeps the token name `gold` so components did not need renaming;
 * its value is the deep green brand colour.
 */
const c = (name: string) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Values live in src/styles/globals.css as RGB channels (light on :root, dark on .dark) so one class works in both
      // themes and opacity modifiers (bg-gold/60) keep working.
      colors: {
        base: { DEFAULT: c('base'), raised: c('base-raised'), card: c('base-card'), elevated: c('base-elevated') },
        // Deep green accent: active nav, primary data series, feature cards. Lighter in dark mode so it stays readable.
        gold: { DEFAULT: c('gold'), muted: c('gold-muted'), faint: c('gold-faint') },
        // Feature (dark green) cards keep white text in both themes.
        feature: { DEFAULT: '#0F4A3A', line: 'rgba(255,255,255,0.14)', tag: '#C7F0A8', muted: 'rgba(255,255,255,0.72)' },
        up: { DEFAULT: c('up'), faint: c('up-faint') },
        down: { DEFAULT: c('down'), text: c('down-text'), faint: c('down-faint') },
        warn: { DEFAULT: c('warn'), faint: c('warn-faint') },
        info: { DEFAULT: c('info'), faint: c('info-faint') },
        neutral: c('neutral'), // meta, labels, axis text (>= 4.5:1 on cards in both themes)
        ink: { DEFAULT: c('ink'), muted: c('ink-muted') }, // primary / secondary text
        line: { DEFAULT: c('line'), soft: c('line-soft'), control: c('line-control') },
        // Text on a filled accent (bg-gold) and on a filled ink surface (bg-ink: primary buttons, tooltips).
        'on-accent': c('on-accent'),
        inverse: c('inverse'),
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { card: '16px' },
      boxShadow: {
        card: 'none',
        float: 'var(--shadow-float)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        'fade-in': 'fade-in 160ms ease-out',
      },
    },
  },
  plugins: [],
} satisfies Config;
