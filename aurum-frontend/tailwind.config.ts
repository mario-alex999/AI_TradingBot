import type { Config } from 'tailwindcss';

/**
 * AURUM AI design tokens — light "private-bank" layout (white sidebar, light grey
 * canvas, white rounded cards, one deep-green accent with dark-green feature cards).
 * Every colour in the UI should come from here.
 *
 * Note: the accent keeps the token name `gold` so components did not need renaming;
 * its value is the deep green brand colour.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: {
          DEFAULT: '#EEEFEE', // canvas
          raised: '#F3F4F3', // filled inputs, wells, quiet fills
          card: '#FFFFFF', // cards and the sidebar
          elevated: '#F7F8F7', // hover, menus
        },
        // Deep green accent: active nav, primary data series, feature cards.
        gold: { DEFAULT: '#0F4A3A', muted: '#1B5E4B', faint: '#E3EFE9' },
        // Feature (dark green) card palette
        feature: { DEFAULT: '#0F4A3A', line: 'rgba(255,255,255,0.14)', tag: '#C7F0A8', muted: 'rgba(255,255,255,0.72)' },
        up: { DEFAULT: '#1A7F52', faint: '#E3F4EA' },
        down: { DEFAULT: '#D64545', text: '#C4242A', faint: '#FDECEC' },
        warn: { DEFAULT: '#B45309', faint: '#FEF3C7' },
        info: { DEFAULT: '#1D4ED8', faint: '#EFF6FF' },
        neutral: '#6B7280', // meta, labels, axis text (>= 4.5:1 on white)
        ink: { DEFAULT: '#111827', muted: '#4B5563' }, // primary / secondary text
        line: { DEFAULT: '#E5E7E6', soft: '#F0F1F0', control: '#D4D7D5' },
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
        float: '0 12px 32px rgba(17,24,39,.12)',
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
