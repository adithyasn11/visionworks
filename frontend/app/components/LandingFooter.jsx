'use client';
import React from 'react';
import Link from 'next/link';

// card-dark, not bg-inverse: the footer is permanently dark in both themes.
// bg-inverse flips with the theme, which turned the footer white in dark mode.
export default function LandingFooter() {
  return (
    <footer className="card-dark pt-16 pb-10 border-t-8 border-accent mt-auto w-full group/footer transition-colors duration-700">
        <div className="max-w-6xl mx-auto px-6 sm:px-8">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-10 mb-12">
            
            {/* Brand Column */}
            <div className="lg:col-span-2">
              <Link href="/" className="flex items-center gap-3 mb-6 cursor-pointer group/logo w-max">
                <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover/logo:rotate-12 transition-transform duration-300">
                  <div className="w-2 h-2 bg-white rounded-sm"></div>
                </div>
                <span className="font-extrabold text-xl tracking-tight group-hover/logo:text-red-500 transition-colors">VisionWorks</span>
              </Link>
              <p className="opacity-80 font-medium text-[14px] leading-relaxed max-w-sm mb-6">
                Enterprise activity analytics built for modern workspaces. Track occupancy, monitor safety, and protect privacy seamlessly.
              </p>
              <div className="flex items-center gap-4">
                <a href="#" className="w-9 h-9 bg-white/10 rounded-full flex items-center justify-center hover:bg-accent hover:-translate-y-1 hover:shadow-lg hover:shadow-red-600/50 transition-all duration-300 group/icon">
                  <span className="font-bold text-xs tracking-widest group-hover/icon:scale-110 transition-transform">X</span>
                </a>
                <a href="#" className="w-9 h-9 bg-white/10 rounded-full flex items-center justify-center hover:bg-accent hover:-translate-y-1 hover:shadow-lg hover:shadow-red-600/50 transition-all duration-300 group/icon2">
                  <span className="font-bold text-xs tracking-widest group-hover/icon2:scale-110 transition-transform">IN</span>
                </a>
              </div>
            </div>

            {/* Links Columns */}
            <div>
              <h4 className="font-bold mb-4 uppercase tracking-[0.12em] text-[10px] opacity-100">Product</h4>
              <ul className="space-y-3">
                <li><Link href="/features" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Overview</Link></li>
                <li><Link href="/security" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Security</Link></li>
                <li><Link href="/dashboard" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Dashboard</Link></li>
              </ul>
            </div>
            
            <div>
              <h4 className="font-bold mb-4 uppercase tracking-[0.12em] text-[10px] opacity-100">Company</h4>
              <ul className="space-y-3">
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">About us</a></li>
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Careers</a></li>
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Contact</a></li>
              </ul>
            </div>

            <div>
              <h4 className="font-bold mb-4 uppercase tracking-[0.12em] text-[10px] opacity-100">Legal</h4>
              <ul className="space-y-3">
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Privacy Policy</a></li>
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Terms of Service</a></li>
                <li><a href="#" className="opacity-80 text-[13px] font-bold hover:opacity-100 hover:translate-x-1 inline-block transition-all duration-300">Cookie Policy</a></li>
              </ul>
            </div>

          </div>

          <div className="pt-6 border-t border-white/10 flex flex-col md:flex-row justify-between items-center gap-6">
            <p className="opacity-75 font-bold text-[12px]">
              &copy; {new Date().getFullYear()} VisionWorks Analytics. All rights reserved.
            </p>
            <div className="flex items-center gap-3 bg-white/10 px-3.5 py-1.5 rounded-full cursor-default hover:bg-white/20 transition-colors">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)] animate-pulse"></span>
              <span className="opacity-90 font-bold text-[10px] tracking-widest uppercase">Operational</span>
            </div>
          </div>
        </div>
      </footer>
  );
}
