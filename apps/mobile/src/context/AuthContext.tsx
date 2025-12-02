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

import React, { createContext, useState, ReactNode, useContext } from 'react';
import Toast from 'react-native-toast-message';
import { useApi } from './ApiContext';
import { endpoints } from '../lib/endpoints';

interface AuthContextType {
  userToken: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<void>;
  logout: () => Promise<void>;
  forgotPassword: (email: string) => Promise<void>;
  verifyOtp: (email: string, otp: string) => Promise<void>;
  resetPassword: (email: string, otp: string, password: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [isLoading, setIsLoading] = useState(false);
  const api = useApi();
  
  // Get token from ApiContext
  const userToken = api.userToken;

  const login = async (email: string, password: string) => {
    setIsLoading(true);
    try {
      const data = await api.post<{ access_token: string }>(endpoints.auth.login, { email, password }, {
        requiresAuth: false, // Login doesn't require auth token
      });

      const token = data.access_token;
      await api.setToken(token);
      
      Toast.show({
        type: 'success',
        text1: 'Welcome back!',
        text2: 'Logged in successfully',
      });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Login failed';
      Toast.show({
        type: 'error',
        text1: 'Login Failed',
        text2: errorMessage,
      });
      throw error; // Re-throw to let UI handle if needed
    } finally {
      setIsLoading(false);
    }
  };

  const signup = async (email: string, password: string, name: string) => {
    setIsLoading(true);
    try {
      await api.post(endpoints.auth.signup, { email, password, name }, {
        requiresAuth: false, // Signup doesn't require auth token
      });

      Toast.show({
        type: 'success',
        text1: 'Account Created',
        text2: 'Please log in with your new account',
      });
      
      // Optional: Auto-login after signup could go here if backend returned a token
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Signup failed';
      Toast.show({
        type: 'error',
        text1: 'Signup Failed',
        text2: errorMessage,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    setIsLoading(true);
    try {
      await api.setToken(null);
      Toast.show({
        type: 'info',
        text1: 'Logged out',
      });
    } catch (error) {
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  const forgotPassword = async (email: string) => {
    setIsLoading(true);
    try {
      await api.post(endpoints.auth.forgotPassword, { email }, {
        requiresAuth: false, // Forgot password doesn't require auth token
      });
      
      Toast.show({
        type: 'success',
        text1: 'Code Sent',
        text2: `Reset code sent to ${email}`,
      });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to send reset code';
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: errorMessage,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const verifyOtp = async (email: string, otp: string) => {
    setIsLoading(true);
    try {
      await api.post(endpoints.auth.verifyOtp, { email, otp }, {
        requiresAuth: false, // OTP verification doesn't require auth token
      });
      
      Toast.show({
        type: 'success',
        text1: 'Verified',
        text2: 'Code verified successfully',
      });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Verification failed';
      Toast.show({
        type: 'error',
        text1: 'Verification Failed',
        text2: errorMessage,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const resetPassword = async (email: string, otp: string, password: string) => {
    setIsLoading(true);
    try {
      await api.post(endpoints.auth.resetPassword, { email, otp, password }, {
        requiresAuth: false, // Reset password doesn't require auth token
      });
      
      Toast.show({
        type: 'success',
        text1: 'Password Reset',
        text2: 'Your password has been updated',
      });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to reset password';
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: errorMessage,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <AuthContext.Provider value={{ 
      login, 
      logout, 
      signup, 
      forgotPassword, 
      verifyOtp, 
      resetPassword,
      isLoading, 
      userToken 
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuthContext = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthContext must be used within an AuthProvider');
  }
  return context;
};
