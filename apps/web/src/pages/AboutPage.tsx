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

export function AboutPage() {
  return (
    <div className="min-h-screen bg-white font-sans text-slate-900 flex flex-col">
      <Navbar />
      <main className="flex-1 max-w-4xl mx-auto px-4 py-20 w-full">
        <h1 className="text-4xl font-bold text-gray-900 mb-8">About Maigie</h1>
        <div className="prose prose-lg text-gray-600">
          <p>
            Maigie is an intelligent study companion designed to help students learn faster, retain more, and achieve their academic goals.
          </p>
          <p>
            Our mission is to democratize access to personalized education through advanced AI technology. We believe that every student deserves a tutor that adapts to their learning style, pace, and needs.
          </p>
          {/* Add more content as needed */}
        </div>
      </main>
      <Footer />
    </div>
  );
}

