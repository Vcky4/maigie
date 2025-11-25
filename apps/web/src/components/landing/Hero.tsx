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

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, Play } from 'lucide-react';
import { DemoInteraction } from './DemoInteraction';
import { useNavigate } from 'react-router-dom';

export function Hero() {
  const [demoStarted, setDemoStarted] = useState(false);
  const navigate = useNavigate();

  return (
    <section className="relative pt-32 pb-10 lg:pt-40 lg:pb-10 overflow-hidden bg-gradient-to-b from-indigo-50/40 to-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="text-center max-w-6xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <h1 className="text-5xl md:text-6xl font-bold tracking-tight text-gray-900 mb-6 leading-tight">
              Your Intelligent <br />
              <span className="text-primary bg-clip-text text-transparent bg-gradient-to-r from-primary to-secondary">
                Study Companion
              </span>
            </h1>
            <p className="text-xl text-gray-600 mb-10 max-w-3xl mx-auto leading-relaxed">
              Maigie helps you organize learning, generate personalized study plans, and master any subject with an AI agent that adapts to your style.
            </p>
            
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button onClick={() => navigate('/waitlist')} className="w-full sm:w-auto bg-primary text-white px-8 py-4 rounded-xl font-semibold text-lg hover:bg-primary/90 transition-all shadow-lg hover:shadow-primary/25 flex items-center justify-center group">
                Start for Free
                <ArrowRight className="ml-2 group-hover:translate-x-1 transition-transform" size={20} />
              </button>
              <button 
                onClick={() => setDemoStarted(true)}
                className="w-full sm:w-auto bg-white text-gray-700 border border-gray-200 px-8 py-4 rounded-xl font-semibold text-lg hover:bg-gray-50 hover:border-gray-300 transition-all flex items-center justify-center group"
              >
                <div className="relative flex items-center justify-center mr-3">
                    <span className="absolute inline-flex h-full w-full rounded-full bg-primary/30 animate-ping"></span>
                    <div className="relative bg-primary/10 p-1.5 rounded-full group-hover:bg-primary/20 transition-colors">
                        <Play className="text-primary fill-current" size={14} />
                    </div>
                </div>
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
              {/* Demo Interaction Component replaces static image */}
              <DemoInteraction 
                isActive={demoStarted} 
                onStart={() => setDemoStarted(true)} 
              />
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
