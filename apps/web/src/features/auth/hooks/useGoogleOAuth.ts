/**
 * Hook for Google OAuth authentication
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import { useAuthStore } from '../store/authStore';
import axios from 'axios';

/**
 * Extract error message from API error response
 */
function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    // Try to extract error message from response
    if (error.response?.data) {
      const data = error.response.data;
      if (typeof data === 'string') {
        return data;
      }
      if (typeof data === 'object') {
        // Check for common error message fields
        return (
          data.detail ||
          data.message ||
          data.error ||
          data.error_description ||
          'Authentication failed. Please try again.'
        );
      }
    }
    // Fallback to status text or message
    return error.response?.statusText || error.message || 'Authentication failed. Please try again.';
  }
  
  if (error instanceof Error) {
    return error.message;
  }
  
  return 'Authentication failed. Please try again.';
}

export function useGoogleOAuth() {
  const navigate = useNavigate();
  const { login } = useAuthStore();
  const [isLoading, setIsLoading] = useState(false);

  const handleGoogleAuth = async () => {
    setIsLoading(true);
    try {
      // Get the current origin for the redirect URI
      // This must match what's configured in Google Cloud Console
      const redirectUri = `${window.location.origin}/auth/oauth/callback`;
      
      // Step 1: Get authorization URL from backend
      // Backend will use this redirect_uri when constructing the Google OAuth URL
      const { authorization_url, state } = await authApi.oauthAuthorize('google', redirectUri);
      
      // Store state in sessionStorage for verification after redirect
      // Backend encodes redirect_uri in the state, so we need to verify it matches
      sessionStorage.setItem('oauth_state', state);
      sessionStorage.setItem('oauth_provider', 'google');
      sessionStorage.setItem('oauth_redirect_uri', redirectUri);
      
      // Step 2: Redirect to Google OAuth
      // Google will redirect back to redirectUri with code and state
      window.location.href = authorization_url;
    } catch (error: unknown) {
      console.error('Google OAuth authorization error:', error);
      setIsLoading(false);
      const errorMessage = extractErrorMessage(error);
      throw new Error(`Failed to initiate Google authentication: ${errorMessage}`);
    }
  };

  const handleOAuthCallback = async (code: string, state: string) => {
    setIsLoading(true);
    try {
      // Verify state matches what we stored
      const storedState = sessionStorage.getItem('oauth_state');
      const provider = sessionStorage.getItem('oauth_provider') || 'google';
      const storedRedirectUri = sessionStorage.getItem('oauth_redirect_uri');
      
      // Validate state to prevent CSRF attacks
      if (!storedState) {
        throw new Error('OAuth session expired. Please try again.');
      }
      
      if (storedState !== state) {
        // Clean up invalid session
        sessionStorage.removeItem('oauth_state');
        sessionStorage.removeItem('oauth_provider');
        sessionStorage.removeItem('oauth_redirect_uri');
        throw new Error('Invalid OAuth state. Security validation failed.');
      }

      // Step 3: Exchange code for access token
      // Backend will extract redirect_uri from state and use it when exchanging code
      const tokenResponse = await authApi.oauthCallback(provider, code, state);
      
      if (!tokenResponse?.access_token) {
        throw new Error('Invalid token response from server.');
      }
      
      // Save token to localStorage first so axios interceptor can use it
      localStorage.setItem('access_token', tokenResponse.access_token);
      
      // Get user data and store auth state
      const user = await authApi.getCurrentUser();
      login(tokenResponse, user);
      
      // Clean up session storage after successful authentication
      sessionStorage.removeItem('oauth_state');
      sessionStorage.removeItem('oauth_provider');
      sessionStorage.removeItem('oauth_redirect_uri');
      
      navigate('/dashboard');
    } catch (error: unknown) {
      console.error('OAuth callback error:', error);
      
      // Clean up session storage on error
      sessionStorage.removeItem('oauth_state');
      sessionStorage.removeItem('oauth_provider');
      sessionStorage.removeItem('oauth_redirect_uri');
      
      setIsLoading(false);
      const errorMessage = extractErrorMessage(error);
      throw new Error(errorMessage);
    }
  };

  return {
    handleGoogleAuth,
    handleOAuthCallback,
    isLoading,
  };
}

