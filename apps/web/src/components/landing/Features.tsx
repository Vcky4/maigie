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
import { MessageSquare, Calendar, Target, Lightbulb, Layers, Zap } from 'lucide-react';

const features = [
  {
    icon: <MessageSquare className="w-6 h-6" />,
    title: "AI Conversational Hub",
    description: "Chat with Maigie to create courses, explain topics, and summarize notes instantly."
  },
  {
    icon: <Calendar className="w-6 h-6" />,
    title: "Smart Scheduling",
    description: "Auto-generate daily study timetables that adapt to your goals and deadlines."
  },
  {
    icon: <Target className="w-6 h-6" />,
    title: "Goal Tracking",
    description: "Set ambitious study goals and let AI break them down into manageable tasks."
  },
  {
    icon: <Lightbulb className="w-6 h-6" />,
    title: "Adaptive Learning",
    description: "Get personalized resource recommendations based on your progress and interests."
  },
  {
    icon: <Layers className="w-6 h-6" />,
    title: "Course Generation",
    description: "Turn any topic into a structured course with modules and quizzes in seconds."
  },
  {
    icon: <Zap className="w-6 h-6" />,
    title: "Voice Interaction",
    description: "Speak to Maigie naturally for hands-free learning support on the go."
  }
];

export function Features() {
  return (
    <section id="features" className="py-24 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className="text-base text-primary font-semibold tracking-wide uppercase">Features</h2>
          <p className="mt-2 text-3xl leading-8 font-bold tracking-tight text-gray-900 sm:text-4xl">
            Everything you need to master your studies
          </p>
          <p className="mt-4 text-lg text-gray-500">
            Maigie combines advanced AI with proven study techniques to help you learn faster and retain more.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {features.map((feature, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
              className="relative p-8 bg-white border border-gray-100 rounded-2xl hover:shadow-lg transition-shadow duration-300 group"
            >
              <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-primary to-secondary transform scale-x-0 group-hover:scale-x-100 transition-transform duration-300 origin-left rounded-t-2xl"></div>
              <div className="inline-flex items-center justify-center p-3 bg-indigo-50 text-primary rounded-xl mb-5 group-hover:bg-primary group-hover:text-white transition-colors duration-300">
                {feature.icon}
              </div>
              <h3 className="text-xl font-bold text-gray-900 mb-3">{feature.title}</h3>
              <p className="text-gray-500 leading-relaxed">
                {feature.description}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
