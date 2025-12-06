/**
 * Hook for Google OAuth authentication
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';

export function useGoogleOAuth() {
  const navigate = useNavigate();
  const { login } = useAuthStore();
  const [isLoading, setIsLoading] = useState(false);

  const handleGoogleAuth = async () => {
    setIsLoading(true);
    try {
      // Get the current origin for the redirect URI
      const redirectUri = `${window.location.origin}/auth/oauth/callback`;
      
      // Step 1: Get authorization URL from backend
      const { authorization_url, state } = await authApi.oauthAuthorize('google', redirectUri);
      
      // Store state in sessionStorage for verification after redirect
      sessionStorage.setItem('oauth_state', state);
      sessionStorage.setItem('oauth_provider', 'google');
      
      // Step 2: Redirect to Google OAuth
      window.location.href = authorization_url;
    } catch (error: any) {
      console.error('Google OAuth error:', error);
      setIsLoading(false);
      throw error;
    }
  };

  const handleOAuthCallback = async (code: string, state: string) => {
    setIsLoading(true);
    try {
      // Verify state matches what we stored
      const storedState = sessionStorage.getItem('oauth_state');
      const provider = sessionStorage.getItem('oauth_provider') || 'google';
      
      if (storedState !== state) {
        throw new Error('Invalid OAuth state');
      }

      // Step 3: Exchange code for access token
      const tokenResponse = await authApi.oauthCallback(provider, code, state);
      
      // Save token to localStorage first so axios interceptor can use it
      localStorage.setItem('access_token', tokenResponse.access_token);
      
      // Get user data and store auth state
      const user = await authApi.getCurrentUser();
      login(tokenResponse, user);
      
      // Clean up session storage
      sessionStorage.removeItem('oauth_state');
      sessionStorage.removeItem('oauth_provider');
      
      navigate('/dashboard');
    } catch (error: any) {
      console.error('OAuth callback error:', error);
      setIsLoading(false);
      throw error;
    }
  };

  return {
    handleGoogleAuth,
    handleOAuthCallback,
    isLoading,
  };
}

