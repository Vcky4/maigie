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
import { useLocalSearchParams, useRouter } from 'expo-router';
import { OtpScreen } from '../../screens/OtpScreen';

type OtpParams = {
  email?: string | string[];
  reason?: string | string[];
};

export default function OtpRoute() {
  const router = useRouter();
  const { email, reason } = useLocalSearchParams<OtpParams>();

  const emailValue = Array.isArray(email) ? email[0] : email;
  const reasonValue = Array.isArray(reason) ? reason[0] : reason;

  return (
    <OtpScreen
      email={emailValue ?? ''}
      reason={reasonValue}
      onNavigate={(screen, params) => {
        if (screen === 'reset-password') {
          router.push({
            pathname: '/auth/reset-password',
            params,
          });
        } else if (screen === 'login') {
          router.replace('/auth');
        } else if (screen === 'forgot-password') {
          router.replace('/auth/forgot-password');
        }
      }}
      onBack={() => {
        if (reasonValue === 'signup-verification') {
          router.replace('/auth');
        } else {
          router.replace('/auth/forgot-password');
        }
      }}
    />
  );
}

