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
import { useRouter } from 'expo-router';
import { AuthScreen } from '../../screens/AuthScreen';

export default function AuthIndex() {
  const router = useRouter();

  return (
    <AuthScreen
      onForgotPassword={() => router.push('/auth/forgot-password')}
      onSignupSuccess={(email) =>
        router.push({
          pathname: '/auth/otp',
          params: { email, reason: 'signup-verification' },
        })
      }
      onLoginSuccess={() => router.push('/dashboard')}
    />
  );
}

