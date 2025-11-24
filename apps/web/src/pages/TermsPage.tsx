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

export function TermsPage() {
  return (
    <div className="min-h-screen bg-white font-sans text-slate-900 flex flex-col">
      <Navbar />
      <main className="flex-1 max-w-4xl mx-auto px-4 py-20 w-full">
        <h1 className="text-4xl font-bold text-gray-900 mb-8">Terms and Conditions</h1>
        <div className="prose prose-lg text-gray-600">
          <p>Last updated: November 2025</p>
          <p>
            Please read these Terms and Conditions carefully before using the Maigie service.
          </p>
          {/* Placeholder for full terms */}
        </div>
      </main>
      <Footer />
    </div>
  );
}

