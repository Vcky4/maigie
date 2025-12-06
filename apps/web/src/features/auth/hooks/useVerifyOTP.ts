/**
 * Hook for OTP verification
 */

import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';
import type { OTPRequest } from '../types/auth.types';

export function useVerifyOTP() {
  const navigate = useNavigate();
  const { login } = useAuthStore();

  return useMutation({
    mutationFn: (data: OTPRequest) => authApi.verifyOTP(data),
    onSuccess: async (_, variables) => {
      // After successful verification, try to login automatically
      // Check if we have password stored (from signup flow)
      const storedPassword = sessionStorage.getItem('temp_password');
      
      if (storedPassword) {
        try {
          // Auto-login after verification
          const tokenResponse = await authApi.login({
            email: variables.email,
            password: storedPassword,
          });
          
          // Save token to localStorage first so axios interceptor can use it
          localStorage.setItem('access_token', tokenResponse.access_token);
          
          // Get user data and store auth state
          const user = await authApi.getCurrentUser();
          login(tokenResponse, user);
          
          // Clear temporary password
          sessionStorage.removeItem('temp_password');
          
          // Navigate to dashboard
          navigate('/dashboard');
        } catch (error) {
          console.error('Auto-login failed:', error);
          // Clear token if auto-login fails
          localStorage.removeItem('access_token');
          // If auto-login fails, redirect to login page
          navigate('/login');
        }
      } else {
        // No password stored (from login flow), redirect to login
        navigate('/login');
      }
    },
  });
}

