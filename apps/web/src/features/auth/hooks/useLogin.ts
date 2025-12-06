/**
 * Hook for user login
 */

import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';
import type { UserLogin } from '../types/auth.types';

export function useLogin() {
  const navigate = useNavigate();
  const { login } = useAuthStore();

  return useMutation({
    mutationFn: (data: UserLogin) => authApi.login(data),
    onSuccess: async (tokenResponse) => {
      // Save token to localStorage first so axios interceptor can use it
      localStorage.setItem('access_token', tokenResponse.access_token);
      
      // Get user data and store auth state
      try {
        const user = await authApi.getCurrentUser();
        login(tokenResponse, user);
        navigate('/dashboard');
      } catch (error) {
        console.error('Failed to get user data:', error);
        // Clear token if getting user data fails
        localStorage.removeItem('access_token');
      }
    },
  });
}

