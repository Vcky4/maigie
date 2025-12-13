import { useQuery } from '@tanstack/react-query';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';
import { useEffect } from 'react';

export function useCurrentUser() {
  const { accessToken, setUser, logout, updateToken } = useAuthStore();

  // Check for token in localStorage if not in store (hydration fix/fallback)
  useEffect(() => {
    if (!accessToken) {
      const storedToken = localStorage.getItem('access_token');
      if (storedToken) {
        updateToken(storedToken);
      }
    }
  }, [accessToken, updateToken]);

  const { data: user, error, isLoading } = useQuery({
    queryKey: ['currentUser'],
    queryFn: authApi.getCurrentUser,
    enabled: !!accessToken || !!localStorage.getItem('access_token'), // Allow fetch if we have a token anywhere
    retry: false,
  });

  useEffect(() => {
    // console.log('useCurrentUser user:', user, accessToken);
    if (user) {
      setUser(user);
    }
  }, [user, setUser]);

  useEffect(() => {
    if (error) {
      // If unauthorized or error, logout
      logout();
    }
  }, [error, logout]);

  return { user, isLoading };
}

