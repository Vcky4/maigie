/**
 * OAuth callback page component
 * Handles the redirect from OAuth providers (Google, etc.)
 */

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useGoogleOAuth } from '../hooks/useGoogleOAuth';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';

export function OAuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { handleOAuthCallback, isLoading } = useGoogleOAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');
    const errorParam = searchParams.get('error');

    // Handle OAuth errors
    if (errorParam) {
      setError('Authentication was cancelled or failed. Please try again.');
      setTimeout(() => {
        navigate('/login');
      }, 3000);
      return;
    }

    // Validate required parameters
    if (!code || !state) {
      setError('Invalid OAuth callback. Missing required parameters.');
      setTimeout(() => {
        navigate('/login');
      }, 3000);
      return;
    }

    // Process the OAuth callback
    handleOAuthCallback(code, state).catch((err) => {
      console.error('OAuth callback error:', err);
      setError(
        err instanceof Error
          ? err.message
          : 'Authentication failed. Please try again.'
      );
      setTimeout(() => {
        navigate('/login');
      }, 3000);
    });
  }, [searchParams, handleOAuthCallback, navigate]);

  return (
    <AuthForm>
      <div className="flex flex-col items-center justify-center min-h-[400px]">
        <AuthLogo />
        {isLoading && !error && (
          <>
            <div className="mt-8">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
            </div>
            <p className="mt-4 text-gray-600">Completing authentication...</p>
          </>
        )}
        {error && (
          <>
            <div className="mt-8 text-red-600 text-center">
              <p className="font-medium">{error}</p>
              <p className="mt-2 text-sm text-gray-600">
                Redirecting to login page...
              </p>
            </div>
          </>
        )}
      </div>
    </AuthForm>
  );
}

