/**
 * Zustand store for authentication state
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { UserResponse, TokenResponse } from '../types/auth.types';

interface AuthState {
  user: UserResponse | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  login: (tokenResponse: TokenResponse, user: UserResponse) => void;
  logout: () => void;
  setUser: (user: UserResponse) => void;
  updateToken: (token: string) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      isAuthenticated: false,

      login: (tokenResponse: TokenResponse, user: UserResponse) => {
        localStorage.setItem('access_token', tokenResponse.access_token);
        set({
          accessToken: tokenResponse.access_token,
          user,
          isAuthenticated: true,
        });
      },

      logout: () => {
        localStorage.removeItem('access_token');
        set({
          accessToken: null,
          user: null,
          isAuthenticated: false,
        });
      },

      setUser: (user: UserResponse) => {
        set({ user });
      },

      updateToken: (token: string) => {
        localStorage.setItem('access_token', token);
        set({ accessToken: token });
      },
    }),
    {
      name: 'auth-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);

