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
          50: '#e8f0fe',
          100: '#d0e2ff',
          400: '#66a3ff',
          500: '#1263FF',
          600: '#0d52d9',
          700: '#0940a3',
          900: '#0B1120',
        },
        primary: {
          50: '#e8f0fe',
          100: '#d0e2ff',
          400: '#66a3ff',
          500: '#1263FF',
          600: '#0d52d9',
          700: '#0940a3',
          900: '#0B1120',
          dark: '#0940a3',
          DEFAULT: '#1263FF',
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
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
        display: ['"Inter"', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
      },
    },
  },
  plugins: [],
}
