/**
 * OAuth callback page component
 * Handles the redirect from OAuth providers (Google, etc.)
 */

import { useEffect, useState, useRef } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useGoogleOAuth } from '../hooks/useGoogleOAuth';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';

export function OAuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { handleOAuthCallback, isLoading } = useGoogleOAuth();
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | null>(null);
  const processedRef = useRef(false);

  useEffect(() => {
    // Prevent multiple executions
    if (processedRef.current) {
      return;
    }

    const code = searchParams.get('code');
    const state = searchParams.get('state');
    const errorParam = searchParams.get('error');
    const errorDescription = searchParams.get('error_description');

    // Handle OAuth provider errors (e.g., user denied access)
    if (errorParam) {
      processedRef.current = true;
      const errorMessage = errorDescription 
        ? `Authentication failed: ${decodeURIComponent(errorDescription)}`
        : 'Authentication was cancelled or failed. Please try again.';
      setError(errorMessage);
      setErrorDetails('You can try again by clicking the button below.');
      return;
    }

    // Validate required parameters
    if (!code || !state) {
      processedRef.current = true;
      setError('Invalid OAuth callback. Missing required parameters.');
      setErrorDetails('The authentication response was incomplete. Please try logging in again.');
      return;
    }

    // Mark as processed before async operation
    processedRef.current = true;

    // Process the OAuth callback
    handleOAuthCallback(code, state).catch((err) => {
      console.error('OAuth callback error:', err);
      const errorMessage = err instanceof Error ? err.message : 'Authentication failed. Please try again.';
      setError(errorMessage);
      setErrorDetails('Please check your connection and try again.');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
            <p className="mt-2 text-sm text-gray-500">Please wait...</p>
          </>
        )}
        {error && (
          <>
            <div className="mt-8 text-center max-w-md">
              <div className="mb-4">
                <svg
                  className="mx-auto h-12 w-12 text-red-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
              </div>
              <p className="text-lg font-medium text-red-600 mb-2">{error}</p>
              {errorDetails && (
                <p className="text-sm text-gray-600 mb-6">{errorDetails}</p>
              )}
              <Link
                to="/login"
                className="inline-block px-6 py-2 bg-primary text-white rounded-full font-medium hover:bg-primary/90 transition-colors"
              >
                Return to Login
              </Link>
            </div>
          </>
        )}
      </div>
    </AuthForm>
  );
}

