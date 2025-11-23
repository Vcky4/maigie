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
import { Check } from 'lucide-react';
import { cn } from '../../lib/utils';

const plans = [
  {
    name: "Free",
    price: "0",
    period: "/mo",
    description: "Essential tools for casual learners.",
    features: [
      "50 AI chat messages/month",
      "Max 2 AI-generated courses",
      "Max 2 active goals",
      "Basic scheduling",
      "Community support"
    ],
    cta: "Get Started",
    popular: false
  },
  {
    name: "Premium Monthly",
    price: "9.99",
    period: "/mo",
    description: "Unlimited access for serious students.",
    features: [
      "Unlimited AI chat",
      "Unlimited AI courses & goals",
      "Advanced scheduling & insights",
      "Voice conversation mode",
      "Priority support"
    ],
    cta: "Start Free Trial",
    popular: false
  },
  {
    name: "Premium Yearly",
    price: "7.99",
    period: "/mo",
    description: "Best value for long-term learning.",
    features: [
      "All Premium features",
      "Save 20% compared to monthly",
      "Early access to new features",
      "Offline mode (Mobile)",
      "Detailed analytics reports"
    ],
    cta: "Start Free Trial",
    popular: true
  }
];

export function Pricing() {
  return (
    <section id="pricing" className="py-24 bg-gray-50 relative overflow-hidden">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className="text-base text-primary font-semibold tracking-wide uppercase">Pricing</h2>
          <p className="mt-2 text-3xl leading-8 font-bold tracking-tight text-gray-900 sm:text-4xl">
            Invest in your knowledge
          </p>
          <p className="mt-4 text-lg text-gray-500">
            Choose the plan that fits your learning journey. Cancel anytime.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
          {plans.map((plan, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
              className={cn(
                "relative p-8 bg-white rounded-2xl border shadow-sm flex flex-col",
                plan.popular ? "border-primary ring-1 ring-primary shadow-lg scale-105 z-10" : "border-gray-200 hover:shadow-md"
              )}
            >
              {plan.popular && (
                <div className="absolute top-0 right-0 bg-gradient-to-r from-primary to-secondary text-white text-xs font-bold px-3 py-1 rounded-bl-lg rounded-tr-xl shadow-sm">
                  MOST POPULAR
                </div>
              )}
              
              <div className="mb-6">
                <h3 className="text-lg font-bold text-gray-900">{plan.name}</h3>
                <p className="text-sm text-gray-500 mt-2 h-10">{plan.description}</p>
              </div>

              <div className="mb-8 flex items-baseline">
                <span className="text-4xl font-extrabold text-gray-900">${plan.price}</span>
                <span className="text-gray-500 ml-1">{plan.period}</span>
              </div>

              <ul className="space-y-4 mb-8 flex-1">
                {plan.features.map((feature, idx) => (
                  <li key={idx} className="flex items-start">
                    <Check className="w-5 h-5 text-success flex-shrink-0 mr-3" />
                    <span className="text-gray-600 text-sm">{feature}</span>
                  </li>
                ))}
              </ul>

              <button className={cn(
                "w-full py-3 px-6 rounded-xl font-semibold transition-all duration-200",
                plan.popular 
                  ? "bg-primary text-white hover:bg-primary/90 shadow-md hover:shadow-lg" 
                  : "bg-gray-50 text-gray-900 hover:bg-gray-100 border border-gray-200"
              )}>
                {plan.cta}
              </button>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
