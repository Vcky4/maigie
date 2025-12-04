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
import { Navbar } from '../components/landing/Navbar';
import { Footer } from '../components/landing/Footer';
import { motion } from 'framer-motion';
import { ArrowRight, Check, Loader2 } from 'lucide-react';
import { createContactInBrevo } from '../services/brevo';

export function WaitlistPage() {
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const result = await createContactInBrevo(email);
      
      if (result.success) {
        setSubmitted(true);
      } else {
        // Still show success to user even if Brevo fails (graceful degradation)
        // But log the error for monitoring
        console.warn('Brevo integration failed, but continuing:', result.error);
        setSubmitted(true);
      }
    } catch (err) {
      // Handle unexpected errors
      console.error('Unexpected error during waitlist signup:', err);
      // Still show success to user (graceful degradation)
      setSubmitted(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white font-sans text-slate-900 flex flex-col">
      <Navbar />
      
      <main className="flex-1 flex flex-col items-center justify-center px-4 py-20 bg-gradient-to-b from-indigo-50/40 to-white">
        <div className="max-w-xl w-full text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-gray-900 mb-6">
              Join the Waitlist
            </h1>
            <p className="text-lg text-gray-600 mb-10">
              Be the first to experience the future of AI-assisted learning. Get early access, exclusive updates, and premium features.
            </p>

            {!submitted ? (
              <form onSubmit={handleSubmit} className="w-full max-w-md mx-auto">
                <div className="flex flex-col space-y-4">
                  <div>
                    <label htmlFor="email" className="sr-only">Email address</label>
                    <input
                      type="email"
                      id="email"
                      name="email"
                      required
                      className="w-full px-5 py-3 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                      placeholder="Enter your email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full flex items-center justify-center px-5 py-3 border border-transparent text-base font-medium rounded-xl text-white bg-primary hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary shadow-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {loading ? (
                      <>
                        <Loader2 className="mr-2 animate-spin" size={20} />
                        Joining...
                      </>
                    ) : (
                      <>
                        Join Waitlist
                        <ArrowRight className="ml-2" size={20} />
                      </>
                    )}
                  </button>
                </div>
                <p className="mt-4 text-xs text-gray-500">
                  By joining, you agree to our Terms of Service and Privacy Policy.
                </p>
              </form>
            ) : (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="bg-green-50 border border-green-100 rounded-2xl p-8 max-w-md mx-auto"
              >
                <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4 text-green-600">
                  <Check size={32} />
                </div>
                <h3 className="text-xl font-bold text-gray-900 mb-2">You're on the list!</h3>
                <p className="text-gray-600">
                  Thanks for joining. We'll be in touch soon with your exclusive access invite.
                </p>
              </motion.div>
            )}
          </motion.div>
        </div>
      </main>

      <Footer />
    </div>
  );
}

