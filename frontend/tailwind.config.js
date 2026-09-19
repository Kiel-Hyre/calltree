/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9', 100: '#eceef2', 200: '#d4d8e0', 300: '#aeb5c4',
          400: '#818ca3', 500: '#626e88', 600: '#4e576e', 700: '#40475a',
          800: '#373d4c', 900: '#181b23', 950: '#0d0f14',
        },
        alert: {
          50: '#fef3f2', 100: '#fee4e2', 300: '#fda29b',
          500: '#f04438', 600: '#d92d20', 700: '#b42318',
        },
        ok: { 50: '#ecfdf3', 100: '#d1fadf', 500: '#12b76a', 600: '#039855', 700: '#027a48' },
        warn: { 50: '#fffaeb', 100: '#fef0c7', 500: '#f79009', 600: '#dc6803', 700: '#b54708' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
