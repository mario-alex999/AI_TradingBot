import type { Config } from 'tailwindcss';

/**
 * AURUM AI design tokens — light "private-bank" layout (white sidebar, light grey
 * canvas, white rounded cards, one deep-green accent with dark-green feature cards),
 * plus a dark theme (class `dark` on <html>) with the same roles.
 * Every colour in the UI should come from here.
 *
 * Note: the accent keeps the token name `gold` so components did not need renaming;
 * its value is the deep green brand colour.
 */
const v = (name: string) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Each colour is a CSS variable (RGB channels) defined in src/styles/globals.css for light and dark,
      // so every utility (bg-, text-, border-, ring-, /opacity) follows the active theme automatically.
      colors: {
        // Text on an `ink` background (tooltips, primary buttons): the card colour of the active theme.
        inverse: v('base-card'),
        // Deep green accent: active nav, primary data series, feature cards.
        gold: { DEFAULT: v('gold'), muted: v('gold-muted'), faint: v('gold-faint') },
        // Feature (dark green) card palette
        feature: { DEFAULT: v('feature'), line: 'rgba(255,255,255,0.14)', tag: v('feature-tag'), muted: 'rgba(255,255,255,0.72)' },
        up: { DEFAULT: v('up'), faint: v('up-faint') },
        down: { DEFAULT: v('down'), text: v('down-text'), faint: v('down-faint') },
        warn: { DEFAULT: v('warn'), faint: v('warn-faint') },
        info: { DEFAULT: v('info'), faint: v('info-faint') },
        neutral: v('neutral'), // meta, labels, axis text (>= 4.5:1 on cards in both themes)
        ink: { DEFAULT: v('ink'), muted: v('ink-muted') }, // primary / secondary text
        line: { DEFAULT: v('line'), soft: v('line-soft'), control: v('line-control') },
      },
      // Surfaces. Background-only on purpose: as a general colour, `base` would also make `text-base`
      // (the 1rem font size) set a text colour.
      backgroundColor: {
        base: {
          DEFAULT: v('base'), // canvas
          raised: v('base-raised'), // filled inputs, wells, quiet fills
          card: v('base-card'), // cards and the sidebar
          elevated: v('base-elevated'), // hover, menus
        },
      },
      ringOffsetColor: { base: v('base') },
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
