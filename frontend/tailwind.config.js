/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'deep-bg': '#0f0f1a',
        'deep-card': '#1a1a2e',
        'deep-border': '#2a2a4a',
        'deep-purple': '#8b5cf6',
        'deep-blue': '#3b82f6',
      },
    },
  },
  plugins: [],
}
