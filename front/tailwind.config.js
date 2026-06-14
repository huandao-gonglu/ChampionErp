const shadeSteps = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]
const colorScale = (name, steps = shadeSteps) => Object.fromEntries(
  steps.map((step) => [step, `rgb(var(--color-${name}-${step}) / <alpha-value>)`]),
)

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: colorScale('primary'),
        brand: colorScale('primary'),
        accent: colorScale('accent'),
        dark: colorScale('dark', [500, 600, 700, 800, 900, 950]),
        success: colorScale('success'),
        info: colorScale('info'),
        warning: colorScale('warning'),
        danger: colorScale('danger'),
      },
      fontFamily: {
        sans: [
          'Inter',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      boxShadow: {
        soft: '0 18px 45px rgb(var(--shadow-color) / 0.08)',
        card: '0 1px 2px rgb(var(--shadow-color) / 0.05), 0 14px 35px rgb(var(--shadow-color) / 0.08)',
        glass: '0 20px 60px rgb(var(--shadow-color) / 0.18)',
        glow: '0 0 24px rgb(var(--color-primary-500) / 0.24)',
      },
      borderRadius: {
        '4xl': '2rem',
      },
    },
  },
  plugins: [],
}
