/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cockpit: {
          bg: '#07090E',
          surface: '#0E131F',
          card: '#131A2B',
          'card-hover': '#182137',
          border: 'rgba(255, 255, 255, 0.08)',
          'border-active': 'rgba(0, 210, 180, 0.4)',
          text: '#F1F5F9',
          muted: '#8E9BB0',
          accent: '#00D2B4',
          'accent-glow': 'rgba(0, 210, 180, 0.15)',
          warning: '#F59E0B',
          danger: '#EF4444',
          success: '#10B981',
          charge: '#38BDF8',
        }
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        'cockpit-glow': '0 0 25px -5px rgba(0, 210, 180, 0.15)',
        'cockpit-card': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
      }
    },
  },
  plugins: [],
}
