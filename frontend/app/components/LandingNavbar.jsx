'use client';
import React from 'react';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';

export default function LandingNavbar() {
  return (
    <header className="sticky top-0 z-50 bg-white/90 backdrop-blur-md border-b border-gray-200 animate-fade-in-up" style={{ animationDelay: '0ms', opacity: 0 }}>
        <div className="max-w-7xl mx-auto px-8 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-3 group cursor-pointer">
            <div className="w-8 h-8 bg-red-600 flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover:shadow-red-600/50 group-hover:scale-110 transition-all duration-300">
              <div className="w-2.5 h-2.5 bg-white rounded-sm"></div>
            </div>
            <span className="font-extrabold text-2xl tracking-tight text-gray-900 group-hover:text-red-600 transition-colors duration-300">VisionWorks</span>
          </Link>
          
          <nav className="hidden md:flex items-center gap-10 font-bold text-sm text-gray-600">
            <Link href="/features" className="hover:text-red-600 hover:-translate-y-0.5 transition-all duration-300">Features</Link>
            <Link href="/security" className="hover:text-red-600 hover:-translate-y-0.5 transition-all duration-300">Security</Link>
          </nav>
          
          <Link 
            href="/dashboard"
            className="bg-black text-white px-6 py-2.5 rounded-xl text-sm font-bold hover:bg-red-600 hover:shadow-lg hover:shadow-red-600/30 hover:-translate-y-0.5 transition-all duration-300 flex items-center gap-2 group"
          >
            Log in <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
          </Link>
        </div>
      </header>
  );
}
