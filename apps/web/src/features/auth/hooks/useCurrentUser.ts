import { useQuery } from '@tanstack/react-query';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';
import { useEffect } from 'react';

export function useCurrentUser() {
  const { accessToken, setUser, logout } = useAuthStore();

  const { data: user, error, isLoading } = useQuery({
    queryKey: ['currentUser'],
    queryFn: authApi.getCurrentUser,
    enabled: !!accessToken, // Only fetch if we have a token
    retry: false,
  });

  useEffect(() => {
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

