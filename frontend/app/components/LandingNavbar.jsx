'use client';
import React from 'react';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { ThemeToggle } from './ThemeToggle';

export default function LandingNavbar() {
  return (
    <header className="themed sticky top-0 z-50 bg-ground/90 backdrop-blur-md border-b border-line animate-fade-in-up" style={{ animationDelay: '0ms' }}>
        <div className="max-w-6xl mx-auto px-6 sm:px-8 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5 group cursor-pointer">
            <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover:shadow-red-600/50 group-hover:scale-110 transition-all duration-300">
              <div className="w-2 h-2 bg-white rounded-sm"></div>
            </div>
            <span className="font-extrabold text-lg tracking-tight text-ink group-hover:text-accent transition-colors duration-300">VisionWorks</span>
          </Link>

          <nav className="hidden md:flex items-center gap-8 font-bold text-[13px] text-ink-muted">
            <Link href="/features" className="hover:text-accent hover:-translate-y-0.5 transition-all duration-300">Features</Link>
            <Link href="/security" className="hover:text-accent hover:-translate-y-0.5 transition-all duration-300">Security</Link>
          </nav>

          <div className="flex items-center gap-2.5">
            <ThemeToggle />
            <Link
              href="/login"
              className="bg-inverse text-inverse px-4 py-2 rounded-xl text-[13px] font-bold hover:bg-accent hover:text-white hover:shadow-lg hover:shadow-red-600/30 hover:-translate-y-0.5 transition-all duration-300 flex items-center gap-1.5 group"
            >
              Log in <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
            </Link>
          </div>
        </div>
      </header>
  );
}
