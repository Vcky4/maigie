import { ReactNode } from 'react';
import { useCurrentUser } from '../features/auth/hooks/useCurrentUser';

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  // This hook will automatically fetch the user if a token exists
  // and update the store or logout on error
  useCurrentUser();

  return <>{children}</>;
}

