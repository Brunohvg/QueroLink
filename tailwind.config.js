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
          50: '#EEF4FF',
          100: '#D9E6FF',
          200: '#B3CCFF',
          300: '#7DA8FF',
          400: '#4585FF',
          500: '#1463FF',
          600: '#0F4FD1',
          700: '#0B3CA3',
          800: '#082C78',
          900: '#051D52',
        },
        graphite: '#111827',
        accent: '#15CFEA',
        surface: '#F5F7FA',
        primary: {
          50: '#EEF4FF',
          100: '#D9E6FF',
          200: '#B3CCFF',
          300: '#7DA8FF',
          400: '#4585FF',
          500: '#1463FF',
          600: '#0F4FD1',
          700: '#0B3CA3',
          800: '#082C78',
          900: '#051D52',
          dark: '#0B3CA3',
          DEFAULT: '#1463FF',
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
        display: ['"Space Grotesk"', '"Inter"', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
      },
    },
  },
  plugins: [],
}
