'use client';
import React from 'react';
import Link from 'next/link';

export default function LandingFooter() {
  return (
    <footer className="bg-black text-white pt-20 pb-12 border-t-8 border-red-600 mt-auto w-full group/footer hover:border-white transition-colors duration-700">
        <div className="max-w-7xl mx-auto px-8">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-12 mb-16">
            
            {/* Brand Column */}
            <div className="lg:col-span-2">
              <Link href="/" className="flex items-center gap-3 mb-8 cursor-pointer group/logo w-max">
                <div className="w-8 h-8 bg-red-600 flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover/logo:rotate-12 transition-transform duration-300">
                  <div className="w-2.5 h-2.5 bg-white rounded-sm"></div>
                </div>
                <span className="font-extrabold text-3xl tracking-tight text-white group-hover/logo:text-red-500 transition-colors">VisionWorks</span>
              </Link>
              <p className="text-gray-400 font-medium text-sm leading-relaxed max-w-sm mb-8">
                Enterprise activity analytics built for modern workspaces. Track occupancy, monitor safety, and protect privacy seamlessly.
              </p>
              <div className="flex items-center gap-4">
                <a href="#" className="w-10 h-10 bg-gray-900 rounded-full flex items-center justify-center hover:bg-red-600 hover:-translate-y-2 hover:shadow-lg hover:shadow-red-600/50 transition-all duration-300 group/icon">
                  <span className="font-bold text-xs tracking-widest group-hover/icon:scale-110 transition-transform">X</span>
                </a>
                <a href="#" className="w-10 h-10 bg-gray-900 rounded-full flex items-center justify-center hover:bg-red-600 hover:-translate-y-2 hover:shadow-lg hover:shadow-red-600/50 transition-all duration-300 group/icon2">
                  <span className="font-bold text-xs tracking-widest group-hover/icon2:scale-110 transition-transform">IN</span>
                </a>
              </div>
            </div>

            {/* Links Columns */}
            <div>
              <h4 className="font-bold text-white mb-6 uppercase tracking-widest text-[10px]">Product</h4>
              <ul className="space-y-4">
                <li><Link href="/features" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Overview</Link></li>
                <li><Link href="/security" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Security</Link></li>
                <li><Link href="/dashboard" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Dashboard</Link></li>
              </ul>
            </div>
            
            <div>
              <h4 className="font-bold text-white mb-6 uppercase tracking-widest text-[10px]">Company</h4>
              <ul className="space-y-4">
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">About us</a></li>
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Careers</a></li>
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Contact</a></li>
              </ul>
            </div>

            <div>
              <h4 className="font-bold text-white mb-6 uppercase tracking-widest text-[10px]">Legal</h4>
              <ul className="space-y-4">
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Privacy Policy</a></li>
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Terms of Service</a></li>
                <li><a href="#" className="text-gray-400 text-sm font-bold hover:text-white hover:translate-x-1 inline-block transition-all duration-300">Cookie Policy</a></li>
              </ul>
            </div>

          </div>

          <div className="pt-8 border-t border-gray-900 flex flex-col md:flex-row justify-between items-center gap-6">
            <p className="text-gray-500 font-bold text-xs">
              &copy; {new Date().getFullYear()} VisionWorks Analytics. All rights reserved.
            </p>
            <div className="flex items-center gap-3 bg-gray-900 px-4 py-2 rounded-full cursor-default hover:bg-gray-800 transition-colors">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)] animate-pulse"></span>
              <span className="text-gray-300 font-bold text-[10px] tracking-widest uppercase">Operational</span>
            </div>
          </div>
        </div>
      </footer>
  );
}
