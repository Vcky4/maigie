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
import { motion } from 'framer-motion';
import { ArrowRight, Play } from 'lucide-react';

export function Hero() {
  return (
    <section className="relative pt-32 pb-20 lg:pt-40 lg:pb-28 overflow-hidden bg-gradient-to-b from-indigo-50/40 to-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="text-center max-w-4xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <span className="inline-block py-1 px-3 rounded-full bg-indigo-50 text-primary text-sm font-semibold mb-6 border border-indigo-100">
              Reimagining how you learn
            </span>
            <h1 className="text-5xl md:text-6xl font-bold tracking-tight text-gray-900 mb-6 leading-tight">
              Your Intelligent <br />
              <span className="text-primary bg-clip-text text-transparent bg-gradient-to-r from-primary to-secondary">
                Study Companion
              </span>
            </h1>
            <p className="text-xl text-gray-600 mb-10 max-w-2xl mx-auto leading-relaxed">
              Maigie helps you organize learning, generate personalized study plans, and master any subject with an AI assistant that adapts to your style.
            </p>
            
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button className="w-full sm:w-auto bg-primary text-white px-8 py-4 rounded-xl font-semibold text-lg hover:bg-primary/90 transition-all shadow-lg hover:shadow-primary/25 flex items-center justify-center group">
                Start for Free
                <ArrowRight className="ml-2 group-hover:translate-x-1 transition-transform" size={20} />
              </button>
              <button className="w-full sm:w-auto bg-white text-gray-700 border border-gray-200 px-8 py-4 rounded-xl font-semibold text-lg hover:bg-gray-50 hover:border-gray-300 transition-all flex items-center justify-center">
                <Play className="mr-2 text-primary fill-current" size={16} />
                Watch Demo
              </button>
            </div>
          </motion.div>

          <motion.div 
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="mt-20 relative"
          >
            <div className="bg-white rounded-2xl shadow-2xl border border-gray-200/60 p-2 overflow-hidden">
              <div className="rounded-xl bg-gray-50 aspect-[16/9] flex items-center justify-center relative overflow-hidden group cursor-pointer">
                 {/* Abstract UI Placeholder */}
                 <div className="absolute inset-0 bg-gradient-to-br from-gray-50 to-indigo-50/50"></div>
                 <div className="relative z-10 text-center">
                    <div className="w-24 h-24 bg-white rounded-full flex items-center justify-center shadow-lg mb-4 mx-auto group-hover:scale-110 transition-transform">
                        <Play className="text-primary ml-1" size={32} fill="currentColor" />
                    </div>
                    <p className="text-gray-500 font-medium">See Maigie in Action</p>
                 </div>
                 {/* Decorative elements mimicking UI */}
                 <div className="absolute top-4 left-4 right-4 h-8 bg-white rounded-lg shadow-sm opacity-60"></div>
                 <div className="absolute top-16 left-4 w-1/4 bottom-4 bg-white rounded-lg shadow-sm opacity-60"></div>
                 <div className="absolute top-16 right-4 w-2/3 bottom-4 bg-white rounded-lg shadow-sm opacity-60"></div>
              </div>
            </div>
            {/* Background blur decoration */}
            <div className="absolute -top-10 -left-10 w-72 h-72 bg-purple-300 rounded-full mix-blend-multiply filter blur-3xl opacity-20 animate-blob"></div>
            <div className="absolute -top-10 -right-10 w-72 h-72 bg-yellow-300 rounded-full mix-blend-multiply filter blur-3xl opacity-20 animate-blob animation-delay-2000"></div>
            <div className="absolute -bottom-20 left-20 w-72 h-72 bg-pink-300 rounded-full mix-blend-multiply filter blur-3xl opacity-20 animate-blob animation-delay-4000"></div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
