/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
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
