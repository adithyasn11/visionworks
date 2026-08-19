// frontend/app/layout.jsx
import { Suspense } from 'react';
import { Inter } from 'next/font/google';
import './globals.css';
import RouteProgress from './components/RouteProgress';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Vision-Based Workplace Activity Analytics System',
  description: 'Real-time CCTV AI analytics, posture detection, and zone occupancy metrics dashboard.',
};

// Runs before first paint so the correct theme is already applied and the page
// never flashes light-then-dark. Kept inline and dependency-free on purpose.
const themeInit = `
(function(){
  try {
    var stored = localStorage.getItem('theme');
    var dark = stored ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className={`${inter.className} min-h-screen`}>
        <Suspense fallback={null}>
          <RouteProgress />
        </Suspense>
        {children}
      </body>
    </html>
  );
}
