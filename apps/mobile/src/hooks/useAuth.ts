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

import { useState } from 'react';
import Toast from 'react-native-toast-message';
import { useAuthContext } from '../context/AuthContext';

export const useAuth = () => {
  const { login, signup, isLoading, resendOtp } = useAuthContext();
  
  const [isSignUp, setIsSignUp] = useState(false);
  
  // Form State
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const handleAuth = async (onSignupSuccess?: (email: string) => void, onLoginSuccess?: () => void) => {
    if (!email || !password || (isSignUp && !name)) {
      Toast.show({
        type: 'error',
        text1: 'Validation Error',
        text2: 'Please fill in all fields',
      });
      return;
    }

    //if password isn't up to 8 characters, show error
    if (password.length < 8) {
      Toast.show({
        type: 'error',
        text1: 'Validation Error',
        text2: 'Password must be at least 8 characters long',
      });
      return;
    }

    try {
      if (isSignUp) {
        await signup(email, password, name);
        if (onSignupSuccess) {
          onSignupSuccess(email);
        } else {
          setIsSignUp(false);
        }
      } else {
        await login(email, password);
        if (onLoginSuccess) {
          onLoginSuccess();
        }
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'An unknown error occurred';

      if (errorMessage === 'Account inactive. Please verify your email.') {
        try {
          await resendOtp(email);
          if (onSignupSuccess) {
            onSignupSuccess(email);
          }
        } catch (resendError) {
          console.error('Failed to resend OTP:', resendError);
        }
        return;
      }

      Toast.show({
        type: 'error',
        text1: 'Authentication Failed',
        text2: errorMessage,
      });
      throw error;
    }
  };

  return {
    isSignUp,
    setIsSignUp,
    loading: isLoading,
    name,
    setName,
    email,
    setEmail,
    password,
    setPassword,
    handleAuth,
  };
};
