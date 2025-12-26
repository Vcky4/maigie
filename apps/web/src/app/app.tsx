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
import { BrowserRouter as Router, Routes as RouterRoutes, Route as RouterRoute } from 'react-router-dom';

// Workaround for React 18 type definition mismatch with react-router-dom
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Routes = RouterRoutes as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Route = RouterRoute as any;
import { LandingPage } from '../pages/LandingPage';
import { WaitlistPage } from '../pages/WaitlistPage';
import { AboutPage } from '../pages/AboutPage';
import { ContactPage } from '../pages/ContactPage';
import { PrivacyPage } from '../pages/PrivacyPage';
import { TermsPage } from '../pages/TermsPage';
import { AuthRedirectPage } from '../pages/AuthRedirectPage';
import { CookieNotice } from '../components/layout/CookieNotice';
import { SignupPage } from '../features/auth/pages/SignupPage';
import { LoginPage } from '../features/auth/pages/LoginPage';
import { OTPVerificationPage } from '../features/auth/pages/OTPVerificationPage';
import { ForgotPasswordPage } from '../features/auth/pages/ForgotPasswordPage';
import { ResetPasswordPage } from '../features/auth/pages/ResetPasswordPage';
import { OAuthCallbackPage } from '../features/auth/pages/OAuthCallbackPage';
import { DashboardPage } from '../pages/DashboardPage';
import { SettingsPage } from '../pages/SettingsPage';
import { SubscriptionSuccessPage } from '../pages/SubscriptionSuccessPage';
import { SubscriptionCancelPage } from '../pages/SubscriptionCancelPage';
import { CourseListPage } from '../features/courses/pages/CourseListPage';
import { CourseDetailPage } from '../features/courses/pages/CourseDetailPage';
import { CourseCreatePage } from '../features/courses/pages/CourseCreatePage';
import { TopicPage } from '../features/courses/pages/TopicPage';
import { NotesPage } from '../features/notes/pages/NotesPage';
import { NoteDetailPage } from '../features/notes/pages/NoteDetailPage';
import { DashboardLayout } from '../components/layout/DashboardLayout';
import { RedirectIfAuthenticated } from '../components/auth/RedirectIfAuthenticated';
import { ProtectedRoute } from '../components/auth/ProtectedRoute';
import { PageContextProvider } from '../features/courses/contexts/PageContext';
import '../styles.css';

export function App() {
  return (
    <PageContextProvider>
      <Router>
        <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/waitlist" element={<WaitlistPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/terms" element={<TermsPage />} />
        {/* Auth routes - redirect if already authenticated */}
        <Route path="/signup" element={<RedirectIfAuthenticated><SignupPage /></RedirectIfAuthenticated>} />
        <Route path="/login" element={<RedirectIfAuthenticated><LoginPage /></RedirectIfAuthenticated>} />
        <Route path="/auth/oauth/callback" element={<OAuthCallbackPage />} />
        <Route path="/verify-otp" element={<RedirectIfAuthenticated><OTPVerificationPage /></RedirectIfAuthenticated>} />
        <Route path="/forgot-password" element={<RedirectIfAuthenticated><ForgotPasswordPage /></RedirectIfAuthenticated>} />
        <Route path="/reset-password" element={<RedirectIfAuthenticated><ResetPasswordPage /></RedirectIfAuthenticated>} />
        
        {/* Protected routes */}
        <Route path="/dashboard" element={<ProtectedRoute><DashboardLayout><DashboardPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><DashboardLayout><SettingsPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/subscription" element={<ProtectedRoute><DashboardLayout><SettingsPage /></DashboardLayout></ProtectedRoute>} /> {/* Legacy route redirect */}
        <Route path="/subscription/success" element={<ProtectedRoute><DashboardLayout><SubscriptionSuccessPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/subscription/cancel" element={<ProtectedRoute><DashboardLayout><SubscriptionCancelPage /></DashboardLayout></ProtectedRoute>} />

        {/* Course Routes */}
        <Route path="/courses" element={<ProtectedRoute><DashboardLayout><CourseListPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/courses/new" element={<ProtectedRoute><DashboardLayout><CourseCreatePage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/courses/:id" element={<ProtectedRoute><DashboardLayout><CourseDetailPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/courses/:courseId/modules/:moduleId/topics/:topicId" element={<ProtectedRoute><DashboardLayout><TopicPage /></DashboardLayout></ProtectedRoute>} />
        
        {/* Note Routes */}
        <Route path="/notes" element={<ProtectedRoute><DashboardLayout><NotesPage /></DashboardLayout></ProtectedRoute>} />
        <Route path="/notes/:noteId" element={<ProtectedRoute><DashboardLayout><NoteDetailPage /></DashboardLayout></ProtectedRoute>} />

        <Route path="/auth/mobile/callback" element={<AuthRedirectPage />} />
      </Routes>
      <CookieNotice />
    </Router>
    </PageContextProvider>
  );
}

export default App;
