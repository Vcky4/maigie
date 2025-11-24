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

export function PrivacyPage() {
  return (
    <div className="min-h-screen bg-white font-sans text-slate-900 flex flex-col">
      <Navbar />
      <main className="flex-1 max-w-4xl mx-auto px-4 py-20 w-full">
        <h1 className="text-4xl font-bold text-gray-900 mb-8">Privacy Policy</h1>
        <div className="prose prose-lg text-gray-600">
          <p className="text-sm text-gray-500 mb-8">Last updated: November 24, 2025</p>

          <p>
            At Maigie ("we," "us," or "our"), we are committed to protecting your privacy. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you visit our website [maigie.com] and use our AI-powered study companion application (collectively, the "Service"). Please read this privacy policy carefully. If you do not agree with the terms of this privacy policy, please do not access the site.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">1. Information We Collect</h2>
          
          <h3 className="text-xl font-medium text-gray-900 mt-6 mb-2">Personal Data</h3>
          <p>
            We may collect personally identifiable information, such as your name, email address, and educational level, when you register for an account, sign up for our newsletter, or contact us. If you choose to sign in via a third-party service (e.g., Google), we may collect information from that service as permitted by your settings.
          </p>

          <h3 className="text-xl font-medium text-gray-900 mt-6 mb-2">Study Data & AI Interactions</h3>
          <p>
            To provide our core services, we collect:
          </p>
          <ul className="list-disc pl-6 space-y-2">
            <li><b>Conversational Data:</b> The text and voice queries you submit to Maigie.</li>
            <li><b>Study Content:</b> Notes, course outlines, goals, schedules, and other materials you create or upload.</li>
            <li><b>Usage Metrics:</b> Data on how you interact with the AI, your study habits, retention rates, and progress.</li>
          </ul>
          <p className="mt-2">
            This data is used to personalize your learning experience and improve our AI models.
          </p>

          <h3 className="text-xl font-medium text-gray-900 mt-6 mb-2">Derivative Data</h3>
          <p>
            Information our servers automatically collect when you access the Site, such as your IP address, your browser type, your operating system, your access times, and the pages you have viewed directly before and after accessing the Site.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">2. How We Use Your Information</h2>
          <p>
            Having accurate information about you permits us to provide you with a smooth, efficient, and customized experience. Specifically, we use information collected about you via the Site to:
          </p>
          <ul className="list-disc pl-6 space-y-2">
            <li>Create and manage your account.</li>
            <li>Generate personalized study plans, courses, and resource recommendations.</li>
            <li>Process payments and manage subscriptions (via secure third-party processors like Stripe).</li>
            <li>Email you regarding your account, security alerts, and product updates.</li>
            <li>Compile anonymous statistical data and analysis for use internally or with third parties.</li>
            <li>Monitor and analyze usage and trends to improve your experience with the Service.</li>
            <li>Prevent fraudulent transactions, monitor against theft, and protect against criminal activity.</li>
          </ul>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">3. Disclosure of Your Information</h2>
          <p>
            We do not sell your personal data. We may share information we have collected about you in certain situations. Your information may be disclosed as follows:
          </p>

          <h3 className="text-xl font-medium text-gray-900 mt-6 mb-2">By Law or to Protect Rights</h3>
          <p>
            If we believe the release of information about you is necessary to respond to legal process, to investigate or remedy potential violations of our policies, or to protect the rights, property, and safety of others, we may share your information as permitted or required by any applicable law, rule, or regulation.
          </p>

          <h3 className="text-xl font-medium text-gray-900 mt-6 mb-2">Third-Party Service Providers</h3>
          <p>
            We may share your information with third parties that perform services for us or on our behalf, including payment processing, data analysis, email delivery, hosting services, customer service, and marketing assistance. These providers are bound by confidentiality agreements.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">4. AI and Data Processing</h2>
          <p>
            Maigie utilizes advanced Large Language Models (LLMs) to process your inputs. While we strive to ensure data privacy:
          </p>
          <ul className="list-disc pl-6 space-y-2">
            <li>Data sent to LLM providers is anonymized where possible.</li>
            <li>We do not use your personal private data to train public foundation models without your explicit consent.</li>
            <li>Please avoid entering sensitive personal information (like financial data or health records) into the AI chat interface.</li>
          </ul>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">5. Security of Your Information</h2>
          <p>
            We use administrative, technical, and physical security measures to help protect your personal information. While we have taken reasonable steps to secure the personal information you provide to us, please be aware that despite our efforts, no security measures are perfect or impenetrable, and no method of data transmission can be guaranteed against any interception or other type of misuse.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">6. Policy for Children</h2>
          <p>
            We do not knowingly solicit information from or market to children under the age of 13. If you become aware of any data we have collected from children under age 13, please contact us using the contact information provided below.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">7. Changes to This Privacy Policy</h2>
          <p>
            We may update this Privacy Policy from time to time. The updated version will be indicated by an updated "Revised" date and the updated version will be effective as soon as it is accessible. We encourage you to review this Privacy Policy frequently to be informed of how we are protecting your information.
          </p>

          <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">8. Contact Us</h2>
          <p>
            If you have questions or comments about this Privacy Policy, please contact us at:
          </p>
          <p className="font-medium text-gray-900 mt-2">
            Maigie Inc.<br />
            <a href="mailto:privacy@maigie.com" className="text-primary hover:underline">privacy@maigie.com</a>
          </p>
        </div>
      </main>
      <Footer />
    </div>
  );
}
