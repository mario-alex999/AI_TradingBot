import type { Config } from 'tailwindcss';

/**
 * AURUM AI design tokens. Every colour in the UI should come from here —
 * avoid hard-coded hex values in components.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: {
          DEFAULT: '#080D16', // primary background
          raised: '#101827', // secondary background (shell, sidebar)
          card: '#141F30', // card background
          elevated: '#19263A', // elevated card / hover
        },
        gold: { DEFAULT: '#D6AD60', muted: '#C5A15A', faint: 'rgba(214,173,96,0.10)' },
        up: { DEFAULT: '#16C784', faint: 'rgba(22,199,132,0.12)' },
        // #EA3943 is the brand negative; down-text is a lighter step that keeps 4.5:1
        // contrast for small text on card backgrounds.
        down: { DEFAULT: '#EA3943', text: '#F2606A', faint: 'rgba(234,57,67,0.12)' },
        warn: { DEFAULT: '#E3A13B', faint: 'rgba(227,161,59,0.12)' },
        info: { DEFAULT: '#5B9BEA', faint: 'rgba(91,155,234,0.12)' },
        neutral: '#8B98AA',
        ink: { DEFAULT: '#F1F5F9', muted: '#A7B2C2' },
        line: { DEFAULT: '#273449', soft: '#1D2A3E' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { card: '10px' },
      boxShadow: {
        card: '0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 24px -16px rgba(0,0,0,0.6)',
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
