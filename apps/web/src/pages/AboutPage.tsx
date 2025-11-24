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
import { Navbar } from '../components/landing/Navbar';
import { Footer } from '../components/landing/Footer';
import { motion } from 'framer-motion';
import { Bot, Calendar, Target, Zap, Layers, BarChart } from 'lucide-react';

export function AboutPage() {
  return (
    <div className="min-h-screen bg-white font-sans text-slate-900 flex flex-col">
      <Navbar />
      
      <main className="flex-1">
        {/* Hero Section */}
        <section className="relative py-20 bg-slate-50 overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-full overflow-hidden z-0 pointer-events-none">
            <div className="absolute -top-[20%] -left-[10%] w-[40%] h-[40%] bg-primary/5 rounded-full blur-3xl" />
            <div className="absolute top-[40%] -right-[10%] w-[30%] h-[30%] bg-secondary/5 rounded-full blur-3xl" />
          </div>
          
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 text-center">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
            >
              <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-gray-900 mb-6">
                Redefining How You Learn
              </h1>
              <p className="text-xl text-gray-600 max-w-2xl mx-auto">
                Maigie is an AI‑powered study companion designed to help you organize learning, generate plans, and master any subject.
              </p>
            </motion.div>
          </div>
        </section>

        {/* Main Content */}
        <div className="max-w-5xl mx-auto px-4 py-16 w-full space-y-20">
            
            {/* Mission */}
            <motion.section 
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="grid md:grid-cols-2 gap-12 items-center"
            >
                <div>
                    <h2 className="text-3xl font-bold text-gray-900 mb-6">Our Mission</h2>
                    <div className="prose prose-lg text-gray-600">
                        <p className="mb-4">
                            Education should adapt to the student, not the other way around. In a world of information overload, students often struggle not with finding content, but with organizing it and staying on track.
                        </p>
                        <p>
                            Our mission is to democratize access to personalized education through advanced AI technology. We believe every student deserves a tutor that adapts to their learning style, pace, and specific academic goals.
                        </p>
                    </div>
                </div>
                <div className="bg-indigo-50 rounded-2xl p-8 border border-indigo-100 relative overflow-hidden">
                    <div className="absolute top-0 right-0 w-32 h-32 bg-primary/10 rounded-full blur-2xl transform translate-x-1/2 -translate-y-1/2"></div>
                    <div className="relative z-10">
                        <h3 className="text-xl font-semibold text-indigo-900 mb-4">Built for every learner</h3>
                        <ul className="space-y-3 text-indigo-700">
                            <li className="flex items-center"><span className="w-2 h-2 bg-primary rounded-full mr-3"></span>High School & University Students</li>
                            <li className="flex items-center"><span className="w-2 h-2 bg-primary rounded-full mr-3"></span>Lifelong Learners</li>
                            <li className="flex items-center"><span className="w-2 h-2 bg-primary rounded-full mr-3"></span>Self-paced Researchers</li>
                            <li className="flex items-center"><span className="w-2 h-2 bg-primary rounded-full mr-3"></span>Professionals Upskilling</li>
                        </ul>
                    </div>
                </div>
            </motion.section>

            {/* Core Capabilities */}
            <section>
                <div className="text-center mb-12">
                    <h2 className="text-3xl font-bold text-gray-900 mb-4">Intelligent Capabilities</h2>
                    <p className="text-gray-600 max-w-2xl mx-auto">
                        Maigie combines conversational AI with structured productivity tools to create a seamless learning environment.
                    </p>
                </div>
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {[
                        {
                            icon: <Bot className="w-6 h-6" />,
                            title: "AI Conversational Hub",
                            desc: "Chat with Maigie to create courses, explain difficult topics, summarize notes, and get instant answers."
                        },
                        {
                            icon: <Calendar className="w-6 h-6" />,
                            title: "Smart Scheduling",
                            desc: "Auto-generate daily timetables and study blocks that adapt to your goals, deadlines, and availability."
                        },
                        {
                            icon: <Target className="w-6 h-6" />,
                            title: "Goal Management",
                            desc: "Set ambitious study goals and let AI break them down into manageable, trackable tasks."
                        },
                        {
                            icon: <Layers className="w-6 h-6" />,
                            title: "Course Generation",
                            desc: "Turn any topic into a structured course with modules, topics, and progress tracking instantly."
                        },
                        {
                            icon: <Zap className="w-6 h-6" />,
                            title: "Adaptive Resources",
                            desc: "Get personalized resource recommendations based on your difficulty level and past learning behavior."
                        },
                        {
                            icon: <BarChart className="w-6 h-6" />,
                            title: "Progress Tracking",
                            desc: "Visualize your retention and study time with analytics that help you optimize your routine."
                        }
                    ].map((item, i) => (
                        <motion.div 
                            key={i}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ delay: i * 0.1 }}
                            className="bg-white p-6 rounded-xl border border-gray-100 shadow-sm hover:shadow-md transition-all"
                        >
                            <div className="w-12 h-12 bg-slate-50 rounded-lg flex items-center justify-center text-primary mb-4">
                                {item.icon}
                            </div>
                            <h3 className="text-lg font-bold text-gray-900 mb-2">{item.title}</h3>
                            <p className="text-gray-600 text-sm leading-relaxed">{item.desc}</p>
                        </motion.div>
                    ))}
                </div>
            </section>

            {/* Vision */}
            <motion.section 
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                viewport={{ once: true }}
                className="bg-slate-900 rounded-3xl p-8 md:p-12 text-center text-white relative overflow-hidden"
            >
                <div className="relative z-10 max-w-3xl mx-auto">
                    <h2 className="text-3xl font-bold mb-6">The Future of Learning</h2>
                    <p className="text-slate-300 text-lg mb-8">
                        We are building a future where your study companion grows with you. From voice-enabled study sessions to offline capabilities on mobile, Maigie is constantly evolving to ensure you have the tools to succeed anywhere.
                    </p>
                    <div className="inline-block border border-slate-700 bg-slate-800/50 rounded-full px-4 py-1 text-sm text-slate-400">
                        Join us on this journey
                    </div>
                </div>
                
                {/* Decorative circles */}
                <div className="absolute top-0 left-0 w-64 h-64 bg-primary/20 rounded-full blur-3xl transform -translate-x-1/3 -translate-y-1/3"></div>
                <div className="absolute bottom-0 right-0 w-64 h-64 bg-secondary/20 rounded-full blur-3xl transform translate-x-1/3 translate-y-1/3"></div>
            </motion.section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
