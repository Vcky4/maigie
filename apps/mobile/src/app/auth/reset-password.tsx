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
import { ResetPasswordScreen } from '../../screens/ResetPasswordScreen';

type ResetParams = {
  email?: string | string[];
  otp?: string | string[];
};

export default function ResetPasswordRoute() {
  const router = useRouter();
  const { email, otp } = useLocalSearchParams<ResetParams>();

  const emailValue = Array.isArray(email) ? email[0] : email;
  const otpValue = Array.isArray(otp) ? otp[0] : otp;

  return (
    <ResetPasswordScreen
      email={emailValue ?? ''}
      otp={otpValue ?? ''}
      onNavigate={(screen) => {
        if (screen === 'login') {
          router.replace('/auth');
        }
      }}
    />
  );
}

