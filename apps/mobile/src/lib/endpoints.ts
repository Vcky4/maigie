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

/**
 * API Endpoints
 * Centralized endpoint definitions for the mobile app
 */
const versionPrefix = '/api/v1';

export const endpoints = {
  // Authentication endpoints
  auth: {
    login: `${versionPrefix}/auth/login/json`,
    signup: `${versionPrefix}/auth/signup`,
    logout: `${versionPrefix}/auth/logout`,
    forgotPassword: `${versionPrefix}/auth/forgot-password`,
    verifyOtp: `${versionPrefix}/auth/verify-otp`,
    resetPassword: `${versionPrefix}/auth/reset-password`,
  },
  
  // User endpoints (add more as needed)
  users: {
    me: `${versionPrefix}/users/me`,
    profile: `${versionPrefix}/users/profile`,
    updateProfile: `${versionPrefix}/users/profile`,
  },
  
  // Add more endpoint groups as your API grows
} as const;

// Type helper for endpoint paths
export type EndpointPath = typeof endpoints[keyof typeof endpoints][keyof typeof endpoints[keyof typeof endpoints]];

