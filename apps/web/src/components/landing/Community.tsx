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
import { Github, Code, MessageSquare, Lightbulb, Users, Share2, Star, Zap, ArrowRight } from 'lucide-react';

export function Community() {
  return (
    <section className="py-24 bg-slate-50 relative overflow-hidden">
      {/* Background Elements */}
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden z-0 pointer-events-none">
        <div className="absolute -top-[20%] -left-[10%] w-[40%] h-[40%] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute top-[40%] -right-[10%] w-[30%] h-[30%] bg-secondary/5 rounded-full blur-3xl" />
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className="text-base text-primary font-semibold tracking-wide uppercase">Community</h2>
          <p className="mt-2 text-3xl leading-8 font-bold tracking-tight text-gray-900 sm:text-4xl">
            Join the Maigie Community
          </p>
          <p className="mt-4 text-lg text-gray-500">
            Whether you're building the future or learning from it, there's a place for you here.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-12">
          {/* Developers Card */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
            className="bg-white p-8 rounded-2xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow"
          >
            <div className="flex items-center mb-6">
              <div className="w-12 h-12 bg-gray-900 rounded-lg flex items-center justify-center text-white mr-4">
                <Code size={24} />
              </div>
              <h3 className="text-2xl font-bold text-gray-900">For Developers</h3>
            </div>
            
            <ul className="space-y-4 mb-8">
              <li className="flex items-start">
                <Github className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Contribute on GitHub</span>
              </li>
              <li className="flex items-start">
                <Code className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Build extensions</span>
              </li>
              <li className="flex items-start">
                <MessageSquare className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Join developer discussions</span>
              </li>
              <li className="flex items-start">
                <Lightbulb className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Help shape the future of AI-assisted learning</span>
              </li>
            </ul>

            <a href="https://github.com/Vcky4/maigie/blob/development/README.md" target="_blank" rel="noopener noreferrer" className="inline-flex items-center text-primary font-semibold hover:text-primary/80 transition-colors">
              View Documentation <ArrowRight size={16} className="ml-2" />
            </a>
          </motion.div>

          {/* Students Card */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
            className="bg-white p-8 rounded-2xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow"
          >
            <div className="flex items-center mb-6">
              <div className="w-12 h-12 bg-primary rounded-lg flex items-center justify-center text-white mr-4">
                <Users size={24} />
              </div>
              <h3 className="text-2xl font-bold text-gray-900">For Students</h3>
            </div>
            
            <ul className="space-y-4 mb-8">
              <li className="flex items-start">
                <Star className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Join the waitlist</span>
              </li>
              <li className="flex items-start">
                <Share2 className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Follow on socials</span>
              </li>
              <li className="flex items-start">
                <Users className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Join the community groups</span>
              </li>
              <li className="flex items-start">
                <Zap className="w-5 h-5 text-gray-400 mt-0.5 mr-3 flex-shrink-0" />
                <span className="text-gray-600">Get early access to premium tools</span>
              </li>
            </ul>

            <a href="/waitlist" className="inline-flex items-center text-primary font-semibold hover:text-primary/80 transition-colors">
              Join Waitlist <ArrowRight size={16} className="ml-2" />
            </a>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

