/**
 * Combined auth hook using Zustand store + React Query
 */

import { useAuthStore } from '../store/authStore';

export function useAuth() {
  const { user, isAuthenticated, logout, setUser } = useAuthStore();

  return {
    user,
    isAuthenticated,
    logout,
    setUser,
  };
}

