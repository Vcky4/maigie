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
 * Color palette for the Maigie mobile app
 * Centralized color definitions for consistent theming
 */

export const colors = {
  // Background colors
  background: {
    primary: '#FFFFFF',
    secondary: '#F9FAFB',
    input: '#fff',
  },

  // Text colors
  text: {
    primary: '#111827',
    secondary: '#1F2937',
    tertiary: '#6B7280',
    placeholder: '#9CA3AF',
    white: '#FFFFFF',
  },

  // Border colors
  border: {
    default: '#E5E7EB',
    active: '#4F46E5', // Indigo-600 - blue for active/focused inputs
  },

  // Primary brand colors
  primary: {
    main: '#4F46E5', // Indigo-600
    shadow: '#4F46E5',
  },

  // Google brand colors (for Google sign-in button)
  google: {
    blue: '#4285F4',
    green: '#34A853',
    yellow: '#FBBC05',
    red: '#EA4335',
  },

  // Divider colors
  divider: {
    line: '#E5E7EB',
    text: '#6B7280',
  },
} as const;

