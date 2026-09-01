/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0c12",
          900: "#10131c",
          800: "#161a26",
          700: "#1f2433",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system", "PingFang SC", "Microsoft YaHei",
          "Segoe UI", "Roboto", "Helvetica Neue", "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
