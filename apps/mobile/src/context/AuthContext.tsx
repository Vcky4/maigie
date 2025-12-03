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
import { Linking } from 'react-native';
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
  googleLogin: () => Promise<void>;
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

  const googleLogin = async () => {
    setIsLoading(true);
    try {
      // Step 1: Get authorization URL from backend
      const authorizeResponse = await api.get<{ authorization_url: string; state: string }>(
        endpoints.auth.oauthAuthorize('google'),
        { requiresAuth: false }
      );

      const { authorization_url } = authorizeResponse;

      // Step 2: For mobile apps, we need to handle OAuth via deep linking
      // The backend redirects to its callback URL, which then needs to redirect to our app
      // This requires:
      // 1. Backend to redirect to app deep link after successful OAuth
      // 2. App to handle the deep link and extract token
      
      // Simplified approach: Open browser and let backend handle OAuth
      // Backend should redirect to a deep link like: maigie://oauth-callback?token=...
      // For now, we'll use a web-based approach where backend handles everything
      // and returns token via a redirect URL we can intercept
      
      // Open authorization URL in browser
      const supported = await Linking.canOpenURL(authorization_url);
      
      if (!supported) {
        throw new Error('Cannot open Google authorization URL');
      }

      // Open browser - user authenticates, backend processes callback
      // Backend should redirect to a URL we can intercept (deep link or custom scheme)
      await Linking.openURL(authorization_url);

      Toast.show({
        type: 'info',
        text1: 'Redirecting',
        text2: 'Please complete authentication in your browser',
      });

      // Note: To complete this implementation, you need to:
      // 1. Configure deep linking in app.config.js (scheme: 'maigie')
      // 2. Set up a deep link handler in your app (e.g., in _layout.tsx or a dedicated handler)
      // 3. Backend should redirect to: maigie://oauth-callback?token=ACCESS_TOKEN&state=STATE
      // 4. App extracts token from deep link and stores it
      
      // For a production implementation, consider using expo-auth-session
      // which handles the OAuth flow more elegantly
      
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Google sign-in failed';
      Toast.show({
        type: 'error',
        text1: 'Google Sign-In Failed',
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
      googleLogin,
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
