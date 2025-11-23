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
import { Smartphone, Apple } from 'lucide-react';

export function DownloadApp() {
  return (
    <section className="py-24 bg-gradient-to-b from-indigo-900 to-accent relative overflow-hidden text-white">
      <div className="absolute inset-0 bg-[url('https://images.unsplash.com/photo-1557683316-973673baf926?q=80&w=2929&auto=format&fit=crop')] opacity-10 bg-cover bg-center mix-blend-overlay"></div>
      
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          <motion.div
            initial={{ opacity: 0, x: -50 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
          >
            <h2 className="text-3xl md:text-4xl font-bold mb-6 leading-tight">
              Learning doesn't stop <br />
              <span className="text-primary-foreground/80">when you leave your desk</span>
            </h2>
            <p className="text-lg text-gray-300 mb-8 max-w-lg">
              Take Maigie with you. Review course summaries, voice chat with your AI tutor, and track goals from anywhere.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-4">
              <a
                href="#"
                className="inline-block rounded-2xl bg-white"
                aria-label="Download on the App Store"
              >
                <img
                  src="/assets/ios.png"
                  alt="Download on the App Store"
                  className="h-14 w-auto sm:h-16"
                  style={{ minWidth: 160 }}
                  draggable={false}
                />
              </a>
              
              <a
                href="#"
                className="inline-block"
                aria-label="Get it on Google Play"
              >
                <img
                  src="/assets/google.png"
                  alt="Get it on Google Play"
                  className="h-14 w-auto sm:h-16"
                  style={{ minWidth: 160 }}
                  draggable={false}
                />
              </a>
            </div>
          </motion.div>

          <motion.div
             initial={{ opacity: 0, y: 50 }}
             whileInView={{ opacity: 1, y: 0 }}
             viewport={{ once: true }}
             transition={{ duration: 0.8, delay: 0.2 }}
             className="relative hidden lg:block"
          >
              {/* Mobile App Mockup */}
              <div className="relative mx-auto border-gray-800 bg-gray-800 border-[14px] rounded-[2.5rem] h-[600px] w-[300px] shadow-xl">
                  <div className="h-[32px] w-[3px] bg-gray-800 absolute -start-[17px] top-[72px] rounded-s-lg"></div>
                  <div className="h-[46px] w-[3px] bg-gray-800 absolute -start-[17px] top-[124px] rounded-s-lg"></div>
                  <div className="h-[46px] w-[3px] bg-gray-800 absolute -start-[17px] top-[178px] rounded-s-lg"></div>
                  <div className="h-[64px] w-[3px] bg-gray-800 absolute -end-[17px] top-[142px] rounded-e-lg"></div>
                  <div className="rounded-[2rem] overflow-hidden w-[272px] h-[572px] bg-white dark:bg-gray-800">
                      {/* Screen Content */}
                      <div className="bg-gray-50 w-full h-full flex flex-col">
                          <div className="bg-primary h-32 rounded-b-3xl p-6 pt-12 text-white">
                              <div className="text-xs opacity-80 mb-1">Good Morning,</div>
                              <div className="text-xl font-bold">Ready to study?</div>
                          </div>
                          <div className="p-4 space-y-4">
                             <div className="bg-white p-4 rounded-xl shadow-sm">
                                 <div className="text-xs text-gray-500 mb-2">DAILY GOAL</div>
                                 <div className="w-full bg-gray-100 rounded-full h-2 mb-2">
                                     <div className="bg-success h-2 rounded-full w-3/4"></div>
                                 </div>
                                 <div className="text-sm font-bold">3/4 Tasks Completed</div>
                             </div>
                             <div className="bg-white p-4 rounded-xl shadow-sm flex items-center gap-3">
                                <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center text-primary font-bold">
                                   AI
                                </div>
                                <div>
                                    <div className="text-sm font-bold">Chat with Maigie</div>
                                    <div className="text-xs text-gray-400">Ask about "Physics 101"</div>
                                </div>
                             </div>
                          </div>
                      </div>
                  </div>
              </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
