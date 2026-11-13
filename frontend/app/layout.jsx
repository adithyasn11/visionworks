// frontend/app/layout.jsx
import { Suspense } from 'react';
import { Inter } from 'next/font/google';
import './globals.css';
import RouteProgress from './components/RouteProgress';
import IntroSplash from './components/IntroSplash';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'VisionWorks — Workplace Safety Compliance Monitoring',
  description: 'Camera-based safety compliance: PPE checks, zone rules and human-reviewed verdicts that say how sure they are.',
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
  // The intro (IntroSplash) plays on the first page of a visit only; later pages skip it before paint.
  try {
    if (sessionStorage.getItem('vw-intro-seen')) document.documentElement.classList.add('intro-seen');
    else {
      sessionStorage.setItem('vw-intro-seen', '1');
      // Mark it finished once it has faded, so pages reached by an in-app link after it (Log in, say) do not
      // wait for an intro that is already over.
      setTimeout(function () { document.documentElement.classList.add('intro-seen'); }, 1800);
    }
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
        <IntroSplash />
        <Suspense fallback={null}>
          <RouteProgress />
        </Suspense>
        {children}
      </body>
    </html>
  );
}
