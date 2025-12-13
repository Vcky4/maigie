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
      onRehydrateStorage: () => (state) => {
        // If state is hydrated but empty, check if we have a token in localStorage
        // and try to recover the session state partially
        const token = localStorage.getItem('access_token');
        if (token && (!state || !state.accessToken)) {
          console.log('Recovering session from localStorage token');
          // We can't set state directly here easily without using set inside, 
          // but state is the hydrated state.
          // In newer Zustand, we can just let useCurrentUser handle the fetching
          // if we ensure accessToken is populated.
          
          // However, since we can't easily modify state here, 
          // we rely on the component using useCurrentUser to fetch the user
          // if it sees the token in localStorage.
        }
      },
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);

