'use client';

// frontend/app/components/ThemeToggle.jsx
import React, { useEffect, useState } from 'react';
import { Sun, Moon } from 'lucide-react';

/**
 * Light/dark switch.
 *
 * Default follows the visitor's OS setting; once they click, their choice is
 * saved to localStorage and wins over the OS in both directions. The initial
 * class is applied by the blocking script in layout.jsx, so this component
 * only has to read back what was already decided.
 */
export const ThemeToggle = ({ className = '' }) => {
  const [isDark, setIsDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains('dark'));
    setMounted(true);
    // Enable colour transitions only after first paint, so loading the page
    // in dark mode doesn't visibly fade in from light.
    document.documentElement.classList.add('theme-ready');
  }, []);

  // Keep following the OS until the visitor makes an explicit choice.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e) => {
      if (localStorage.getItem('theme')) return;
      document.documentElement.classList.toggle('dark', e.matches);
      setIsDark(e.matches);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const toggle = (event) => {
    const next = !isDark;
    const root = document.documentElement;

    const apply = () => {
      root.classList.toggle('dark', next);
      setIsDark(next);
      localStorage.setItem('theme', next ? 'dark' : 'light');
    };

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Without View Transitions (or when reduced motion is requested), fall back
    // to the plain colour cross-fade the .themed transition already provides.
    if (reduced || !document.startViewTransition) {
      apply();
      return;
    }

    // Circular reveal that expands from the button the user actually clicked,
    // so the new theme visibly originates from the control.
    const rect = event.currentTarget.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const radius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );

    root.style.setProperty('--reveal-x', `${x}px`);
    root.style.setProperty('--reveal-y', `${y}px`);
    root.style.setProperty('--reveal-r', `${radius}px`);
    root.classList.add('theme-switching');

    const transition = document.startViewTransition(apply);
    transition.finished.finally(() => root.classList.remove('theme-switching'));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={mounted ? `Switch to ${isDark ? 'light' : 'dark'} mode` : 'Switch theme'}
      aria-pressed={mounted ? isDark : undefined}
      title={mounted ? `Switch to ${isDark ? 'light' : 'dark'} mode` : undefined}
      className={`relative w-9 h-9 rounded-xl border border-line bg-surface text-ink-muted flex items-center justify-center hover:text-accent hover:border-[color:var(--accent)] transition-colors duration-200 ${className}`}
    >
      {/* Both icons are rendered and cross-faded so the control never reflows.
          suppressHydrationWarning: the server can't know the visitor's theme. */}
      <Sun
        suppressHydrationWarning
        className={`w-4 h-4 absolute transition-all duration-200 ${
          mounted && isDark ? 'opacity-0 -rotate-90 scale-50' : 'opacity-100 rotate-0 scale-100'
        }`}
      />
      <Moon
        suppressHydrationWarning
        className={`w-4 h-4 absolute transition-all duration-200 ${
          mounted && isDark ? 'opacity-100 rotate-0 scale-100' : 'opacity-0 rotate-90 scale-50'
        }`}
      />
    </button>
  );
};

export default ThemeToggle;
