import type { Config } from 'tailwindcss';

/**
 * AURUM AI design tokens — "data terminal" app style (taste-skill), brand gold
 * kept as the single accent. Colour belongs on data and on the one selection
 * line; chrome stays neutral. Every colour in the UI should come from here.
 *
 * Ground model: true neutral darks (no navy). Cards sit one step DARKER than
 * the ground, separated by a 1px line, never by a shadow.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: {
          DEFAULT: '#171719', // ground (page, sidebar, top bar)
          raised: '#1c1c1f', // fields, wells, quiet fills
          card: '#121215', // cards: one step darker than the ground
          elevated: '#212023', // hover, menus, popovers
        },
        // The one accent: data marks and the 1px selection line. Never a fill on chrome.
        gold: { DEFAULT: '#D6AD60', muted: '#C5A15A', faint: 'rgba(214,173,96,0.10)' },
        up: { DEFAULT: '#16C784', faint: 'rgba(22,199,132,0.12)' },
        // #EA3943 is the brand negative; down-text is a lighter step that keeps 4.5:1
        // contrast for small text on card backgrounds.
        down: { DEFAULT: '#EA3943', text: '#F2606A', faint: 'rgba(234,57,67,0.12)' },
        warn: { DEFAULT: '#D9A441', faint: '#2a2416' },
        info: { DEFAULT: '#8FB4E3', faint: '#1a2230' },
        // Two greys that read apart, both >= 4.5:1 on cards.
        neutral: '#8A8C92', // meta, labels, axis text
        ink: { DEFAULT: '#F2F2F3', muted: '#B4B5BA' }, // primary / secondary text
        line: { DEFAULT: '#26262A', soft: '#1E1E22', control: '#3A3A3E' },
      },
      fontFamily: {
        // One grotesk for text; a monospace for every figure (the style's decision).
        sans: ['"Public Sans"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { card: '6px' },
      boxShadow: {
        // Resting surfaces carry no shadow; only floating layers (menus, dialogs) do.
        card: 'none',
        float: '0 12px 28px rgba(0,0,0,.35)',
      },
      keyframes: {
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        'fade-in': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        shimmer: 'shimmer 1.6s infinite',
        'fade-in': 'fade-in 160ms ease-out',
      },
    },
  },
  plugins: [],
} satisfies Config;
