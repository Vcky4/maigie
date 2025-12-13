/**
 * Component that protects routes requiring authentication
 */

import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../features/auth/store/authStore';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, accessToken } = useAuthStore();
  
  // Check localStorage directly for fallback if store isn't hydrated yet
  const hasStoredToken = typeof window !== 'undefined' && !!localStorage.getItem('access_token');

  useEffect(() => {
    // If not authenticated and no token in storage, redirect to login
    if (!isAuthenticated && !accessToken && !hasStoredToken) {
      // Save the location they were trying to access
      navigate('/login', { state: { from: location } });
    }
  }, [isAuthenticated, accessToken, hasStoredToken, navigate, location]);

  // If we have a stored token but store isn't authenticated yet, 
  // we render children and let the app hydration handle it.
  // Ideally, we'd show a loading spinner here until rehydration completes.
  
  // If definitely not authenticated, don't render content
  if (!isAuthenticated && !accessToken && !hasStoredToken) {
    return null;
  }

  return <>{children}</>;
}
