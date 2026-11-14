// frontend/app/not-found.jsx
// The page for an address that does not exist, in the product's own look (it used to be Next.js's bare default,
// with no way back).
import Link from 'next/link';

export const metadata = { title: 'Page not found · VisionWorks' };

export default function NotFound() {
  return (
    <main className="themed min-h-screen bg-ground text-ink flex items-center justify-center px-6">
      <div className="max-w-md text-center flex flex-col items-center gap-4">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="w-8 h-8 bg-accent flex items-center justify-center rounded-lg">
            <span className="w-2.5 h-2.5 bg-white rounded-sm" />
          </span>
          <span className="font-extrabold text-lg tracking-tight">VisionWorks</span>
        </Link>
        <p className="font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-accent">404</p>
        <h1 className="text-3xl font-black tracking-tight">This page doesn’t exist.</h1>
        <p className="text-[0.9375rem] text-ink-muted leading-relaxed">
          The address may be mistyped, or the page has moved.
        </p>
        <div className="flex flex-wrap justify-center gap-3 mt-2">
          <Link href="/home" className="rounded-xl bg-accent px-5 py-2.5 text-[0.875rem] font-bold text-white hover:brightness-110 transition">
            Go to your dashboard
          </Link>
          <Link href="/" className="rounded-xl border border-line px-5 py-2.5 text-[0.875rem] font-bold text-ink hover:border-[color:var(--accent)] transition">
            Back to the site
          </Link>
        </div>
      </div>
    </main>
  );
}
