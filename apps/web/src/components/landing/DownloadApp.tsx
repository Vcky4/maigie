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

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Mic, BookOpen, Calendar as CalendarIcon, CheckCircle2 } from 'lucide-react';
import { cn } from '../../lib/utils';

const screens = [
  {
    id: 'dashboard',
    title: 'Dashboard',
    color: 'bg-primary',
    content: (
      <div className="p-4 space-y-4">
        <div className="bg-white p-4 rounded-xl shadow-sm">
            <div className="text-xs text-gray-500 mb-2 font-semibold">DAILY GOAL</div>
            <div className="w-full bg-gray-100 rounded-full h-2 mb-2">
                <motion.div 
                  className="bg-success h-2 rounded-full" 
                  initial={{ width: 0 }}
                  animate={{ width: '75%' }}
                  transition={{ duration: 1, delay: 0.2 }}
                />
            </div>
            <div className="text-sm font-bold text-gray-800">3/4 Tasks Completed</div>
        </div>
        <div className="bg-white p-4 rounded-xl shadow-sm flex items-center gap-3">
           <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center text-primary font-bold">
              AI
           </div>
           <div>
               <div className="text-sm font-bold text-gray-800">Chat with Maigie</div>
               <div className="text-xs text-gray-400">Ask about "Physics 101"</div>
           </div>
        </div>
      </div>
    )
  },
  {
    id: 'voice',
    title: 'Voice Mode',
    color: 'bg-purple-600',
    content: (
      <div className="h-full flex flex-col items-center justify-center space-y-8 pb-20">
         <motion.div 
           animate={{ scale: [1, 1.2, 1] }}
           transition={{ repeat: Infinity, duration: 2 }}
           className="w-24 h-24 rounded-full bg-purple-500/20 flex items-center justify-center"
         >
            <div className="w-16 h-16 rounded-full bg-purple-500 flex items-center justify-center shadow-lg shadow-purple-500/40">
              <Mic className="w-8 h-8 text-white" />
            </div>
         </motion.div>
         <div className="flex gap-1 items-end h-8">
            {[...Array(5)].map((_, i) => (
              <motion.div
                key={i}
                className="w-2 bg-purple-400 rounded-full"
                animate={{ height: [10, 24, 10] }}
                transition={{ repeat: Infinity, duration: 0.8, delay: i * 0.1 }}
              />
            ))}
         </div>
         <p className="text-gray-500 font-medium">Listening...</p>
      </div>
    )
  },
  {
    id: 'chat',
    title: 'AI Chat',
    color: 'bg-blue-600',
    content: (
      <div className="p-4 space-y-4 flex flex-col justify-end h-full pb-20">
        <motion.div 
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          className="bg-blue-600 text-white p-3 rounded-2xl rounded-tr-none self-end max-w-[80%] text-sm shadow-sm"
        >
          Explain quantum entanglement simply.
        </motion.div>
        <motion.div 
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.5 }}
          className="bg-white text-gray-800 p-3 rounded-2xl rounded-tl-none self-start max-w-[90%] text-sm shadow-sm border border-gray-100"
        >
          Think of it like two magic coins. No matter how far apart they are, if you flip one and it lands heads, the other will instantly be tails!
        </motion.div>
      </div>
    )
  },
  {
    id: 'course',
    title: 'Generating...',
    color: 'bg-orange-500',
    content: (
      <div className="p-4 h-full flex flex-col justify-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="bg-white rounded-xl overflow-hidden shadow-md border border-gray-100"
        >
          <div className="h-32 bg-orange-100 flex items-center justify-center">
             <BookOpen className="w-12 h-12 text-orange-500" />
          </div>
          <div className="p-4">
             <div className="text-xs font-bold text-orange-500 mb-1 uppercase tracking-wider">New Course</div>
             <h4 className="font-bold text-gray-900 text-lg mb-2">Python for Beginners</h4>
             <div className="space-y-2">
                <div className="flex items-center text-xs text-gray-500">
                   <CheckCircle2 className="w-3 h-3 mr-2 text-green-500" /> 4 Modules
                </div>
                <div className="flex items-center text-xs text-gray-500">
                   <CheckCircle2 className="w-3 h-3 mr-2 text-green-500" /> 12 Quizzes
                </div>
             </div>
          </div>
        </motion.div>
      </div>
    )
  },
  {
    id: 'schedule',
    title: 'Schedule',
    color: 'bg-indigo-600',
    content: (
      <div className="p-4 space-y-3">
        <div className="flex justify-between items-center mb-4">
           <span className="font-bold text-gray-800">Today</span>
           <CalendarIcon className="w-4 h-4 text-gray-400" />
        </div>
        {[
          { time: '09:00 AM', event: 'Math Study Block', color: 'bg-blue-100 border-blue-200 text-blue-700' },
          { time: '11:30 AM', event: 'History Quiz', color: 'bg-red-100 border-red-200 text-red-700' },
          { time: '02:00 PM', event: 'Coding Session', color: 'bg-green-100 border-green-200 text-green-700' },
        ].map((item, i) => (
          <motion.div 
            key={i}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.1 }}
            className={cn("p-3 rounded-lg border-l-4 flex gap-3", item.color.replace('text-', 'border-'))}
          >
             <span className="text-xs font-mono text-gray-500 w-16">{item.time}</span>
             <span className="text-sm font-medium text-gray-800">{item.event}</span>
          </motion.div>
        ))}
      </div>
    )
  }
];

export function DownloadApp() {
  const [activeScreen, setActiveScreen] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setActiveScreen((prev) => (prev + 1) % screens.length);
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <section id="download" className="py-24 bg-gradient-to-b from-indigo-900 to-accent relative overflow-hidden text-white">
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
              {/* App Store Button */}
              <a href="/waitlist" className="group hover:opacity-90 transition-opacity">
                <img 
                  src="/assets/ios.png" 
                  alt="Download on the App Store" 
                  className="h-[50px] w-auto rounded-xl"
                />
              </a>

              {/* Google Play Button */}
              <a href="/waitlist" className="group hover:opacity-90 transition-opacity">
                <img 
                  src="/assets/google.png" 
                  alt="Get it on Google Play" 
                  className="h-[50px] w-auto rounded-xl"
                />
              </a>
            </div>
          </motion.div>

          <motion.div
             initial={{ opacity: 0, y: 50 }}
             whileInView={{ opacity: 1, y: 0 }}
             viewport={{ once: true }}
             transition={{ duration: 0.8, delay: 0.2 }}
             className="relative"
          >
              {/* Mobile App Mockup */}
              <div className="relative mx-auto border-gray-900 bg-gray-900 border-[14px] rounded-[2.5rem] h-[600px] w-[300px] shadow-2xl ring-1 ring-white/10">
                  <div className="h-[32px] w-[3px] bg-gray-800 absolute -start-[17px] top-[72px] rounded-s-lg"></div>
                  <div className="h-[46px] w-[3px] bg-gray-800 absolute -start-[17px] top-[124px] rounded-s-lg"></div>
                  <div className="h-[46px] w-[3px] bg-gray-800 absolute -start-[17px] top-[178px] rounded-s-lg"></div>
                  <div className="h-[64px] w-[3px] bg-gray-800 absolute -end-[17px] top-[142px] rounded-e-lg"></div>
                  
                  <div className="rounded-[2rem] overflow-hidden w-[272px] h-[572px] bg-white relative">
                      
                      {/* Status Bar */}
                      <div className="absolute top-0 w-full h-8 z-20 px-6 flex justify-between items-end pb-1 text-[10px] font-medium text-white/80">
                         <span>9:41</span>
                         <div className="flex gap-1">
                            <div className="w-3 h-3 bg-white rounded-full opacity-80"></div>
                            <div className="w-3 h-3 border border-white rounded-sm opacity-80"></div>
                         </div>
                      </div>

                      {/* Screen Header */}
                      <div className={cn("h-28 rounded-b-3xl p-6 pt-12 text-white transition-colors duration-500 relative z-10", screens[activeScreen].color)}>
                         <AnimatePresence mode="wait">
                            <motion.div
                              key={activeScreen}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              exit={{ opacity: 0, y: -10 }}
                              className="absolute bottom-6 left-6"
                            >
                               <div className="text-xs opacity-80 mb-1">Maigie</div>
                               <div className="text-xl font-bold">{screens[activeScreen].title}</div>
                            </motion.div>
                         </AnimatePresence>
                      </div>

                      {/* Animated Screen Content */}
                      <div className="bg-gray-50 h-full -mt-6 pt-8 relative">
                         <AnimatePresence mode="wait">
                            <motion.div
                               key={activeScreen}
                               initial={{ opacity: 0, x: 20 }}
                               animate={{ opacity: 1, x: 0 }}
                               exit={{ opacity: 0, x: -20 }}
                               transition={{ duration: 0.4 }}
                               className="h-full"
                            >
                               {screens[activeScreen].content}
                            </motion.div>
                         </AnimatePresence>
                      </div>

                      {/* Bottom Nav */}
                      <div className="absolute bottom-0 w-full bg-white border-t border-gray-100 h-16 flex justify-around items-center px-2 pb-2 z-20">
                          {screens.map((_, i) => (
                             <div 
                               key={i} 
                               className={cn(
                                 "w-10 h-10 rounded-full flex items-center justify-center transition-colors duration-300",
                                 i === activeScreen ? "bg-gray-100 text-primary" : "text-gray-300"
                               )}
                             >
                                <div className={cn("w-2 h-2 rounded-full", i === activeScreen ? "bg-current" : "bg-gray-200")} />
                             </div>
                          ))}
                      </div>
                  </div>
              </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
