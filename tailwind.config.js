/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './templates/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef2ff',
          100: '#e0e7ff',
          400: '#6b7fee',
          500: '#4361ee',
          600: '#3051d3',
          700: '#2540ad',
          900: '#1e2a6e',
        },
        primary: {
          50: '#eef2ff',
          100: '#e0e7ff',
          400: '#6b7fee',
          500: '#4361ee',
          600: '#3051d3',
          700: '#2540ad',
          900: '#1e2a6e',
          dark: '#2540ad',
          DEFAULT: '#4361ee',
        },
        success: {
          50: '#ecfdf5',
          100: '#ecfdf5',
          500: '#31c48d',
          700: '#1f9d6e',
          DEFAULT: '#31c48d',
        },
        warning: {
          50: '#fffbeb',
          100: '#fffbeb',
          500: '#d97706',
          700: '#92400e',
          DEFAULT: '#d97706',
        },
        danger: {
          50: '#fef2f2',
          100: '#fef2f2',
          500: '#e53e3e',
          700: '#b91c1c',
          DEFAULT: '#e53e3e',
        },
      },
      fontFamily: {
        sans: ['"Public Sans"', 'system-ui', 'sans-serif'],
        display: ['"Lexend"', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
      },
    },
  },
  plugins: [],
}
