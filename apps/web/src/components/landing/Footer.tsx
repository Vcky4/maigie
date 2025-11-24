/*
 * Maigie - Your Intelligent Study Companion
 * Copyright (C) 2025 Maigie
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import React from 'react';
import { Github, Twitter, Linkedin } from 'lucide-react';

export function Footer() {
  return (
    <footer className="bg-white border-t border-gray-100">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
          <div className="col-span-1 md:col-span-2">
            {/* Logo Image */}
            <img src="/assets/logo.png" alt="Maigie Logo" className="h-8 w-auto mb-4" />
            <p className="text-gray-500 max-w-xs">
              Your AI-powered study companion. Organize, learn, and achieve your goals with an intelligent agent that adapts to you.
            </p>
          </div>
          
          <div>
            <h3 className="text-sm font-semibold text-gray-900 tracking-wider uppercase">Product</h3>
            <ul className="mt-4 space-y-4">
              <li><a href="#features" className="text-gray-500 hover:text-primary">Features</a></li>
              <li><a href="#pricing" className="text-gray-500 hover:text-primary">Pricing</a></li>
              <li><a href="#download" className="text-gray-500 hover:text-primary">Download</a></li>
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-gray-900 tracking-wider uppercase">Company</h3>
            <ul className="mt-4 space-y-4">
              <li><a href="/about" className="text-gray-500 hover:text-primary">About</a></li>
              <li><a href="/contact" className="text-gray-500 hover:text-primary">Contact</a></li>
              <li><a href="/privacy" className="text-gray-500 hover:text-primary">Privacy Policy</a></li>
              <li><a href="/terms" className="text-gray-500 hover:text-primary">Terms & Conditions</a></li>
            </ul>
          </div>
        </div>
        
        <div className="mt-12 border-t border-gray-100 pt-8 flex flex-col md:flex-row justify-between items-center">
          <p className="text-gray-400 text-sm">
            © {new Date().getFullYear()} Maigie. All rights reserved.
          </p>
          <div className="flex space-x-6 mt-4 md:mt-0">
            <a href="https://x.com/Maigieteam?t=p0RDZuVs52aGyn2YCIOrhA&s=09" target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-primary">
              <Twitter size={20} />
            </a>
            <a href="https://github.com/Vcky4/maigie" target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-primary">
              <Github size={20} />
            </a>
            <a href="https://www.linkedin.com/company/maigie-ai" target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-primary">
              <Linkedin size={20} />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
