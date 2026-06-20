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
        success: {
          50: '#ecfdf5',
          500: '#31c48d',
          700: '#1f9d6e',
        },
        warning: {
          50: '#fffbeb',
          500: '#d97706',
          700: '#92400e',
        },
        danger: {
          50: '#fef2f2',
          500: '#e53e3e',
          700: '#b91c1c',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
      },
    },
  },
  plugins: [],
}
