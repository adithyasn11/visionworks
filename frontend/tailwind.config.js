/** @type {import('tailwindcss').Config} */
module.exports = {
  // Class strategy: ThemeToggle stamps `dark` on <html>, so the visitor's
  // choice overrides their OS setting in both directions.
  darkMode: 'class',
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Theme tokens as Tailwind colours, so border-*, divide-* and bg-* utilities exist for them
        // (`divide-line` and `border-line/60` used to generate nothing and fell back to Tailwind's light grey,
        // which showed as bright lines in dark mode).
        line: 'var(--line)',
        'line-soft': 'var(--line-soft)',
        brand: {
          dark: "#000000",
          card: "#FFFFFF",
          border: "#E5E7EB",
          accent: "#DC2626",
          success: "#16A34A",
          warning: "#D97706",
          danger: "#DC2626"
        }
      }
    },
  },
  plugins: [],
}
