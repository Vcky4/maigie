/**
 * Login page component
 */

import { useState, FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { AuthInput } from '../components/AuthInput';
import { PasswordInput } from '../components/PasswordInput';
import { AuthButton } from '../components/AuthButton';
import { GoogleOAuthButton } from '../components/GoogleOAuthButton';
import { AuthDivider } from '../components/AuthDivider';
import { useLogin } from '../hooks/useLogin';
import { useGoogleOAuth } from '../hooks/useGoogleOAuth';

export function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const redirect = searchParams.get('redirect');
  const loginMutation = useLogin();
  const { handleGoogleAuth, isLoading: isGoogleLoading } = useGoogleOAuth();

  const [formData, setFormData] = useState({
    email: '',
    password: '',
  });

  const [errors, setErrors] = useState<Record<string, string>>({});

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) {
      newErrors.email = 'Please enter a valid email address';
    }

    if (!formData.password) {
      newErrors.password = 'Password is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!validateForm()) {
      return;
    }

    try {
      await loginMutation.mutateAsync({
        email: formData.email,
        password: formData.password,
      });
      // Redirect to dashboard or the specified redirect URL
      navigate(redirect || '/dashboard');
    } catch (error: unknown) {
      if (error && typeof error === 'object' && 'response' in error) {
        const errorResponse = error as { 
          response?: { 
            status?: number;
            data?: { detail?: string } 
          } 
        };
        const status = errorResponse.response?.status;
        const errorMessage = errorResponse.response?.data?.detail;
        
        // Check if it's a 400 error with account inactive message
        if (status === 400 && errorMessage === 'Account inactive. Please verify your email.') {
          // Store email for OTP verification page
          localStorage.setItem('signup_email', formData.email);
          // Store password temporarily for auto-login after OTP verification
          sessionStorage.setItem('temp_password', formData.password);
          // Set flag to auto-trigger resend on OTP page
          localStorage.setItem('auto_resend_otp', 'true');
          navigate('/verify-otp');
          return;
        }
        
        setErrors({
          submit: errorMessage || 'Invalid email or password. Please try again.',
        });
      } else {
        setErrors({
          submit: 'Invalid email or password. Please try again.',
        });
      }
    }
  };

  const handleGoogleLogin = async () => {
    try {
      await handleGoogleAuth();
    } catch (error: unknown) {
      // Extract error message - handleGoogleAuth already throws Error with message
      const errorMessage = error instanceof Error 
        ? error.message 
        : 'Google sign-in failed. Please try again.';
      setErrors({
        submit: errorMessage,
      });
    }
  };

  return (
    <AuthForm>
      <div className="flex flex-col flex-1">
        <div className="flex flex-col mb-6 md:items-center">
          <div className="pb-3 -mx-4 -mt-4 px-4 pt-4 border-b border-gray-200 md:mx-0 md:px-0 md:mt-0 md:pt-0 md:border-b-0 md:pb-0">
            <AuthLogo />
          </div>
          <h1 className="text-3xl font-semibold text-charcoal mt-8 md:text-center">
            Welcome back
          </h1>
        </div>
        <p className="text-gray-600 text-center mb-8 md:-mt-6">Log in to your account</p>

        <form onSubmit={handleSubmit} className="space-y-5">
        <AuthInput
          id="email"
          label="Email address"
          type="email"
          placeholder="Email address"
          value={formData.email}
          onChange={(e) => setFormData({ ...formData, email: e.target.value })}
          error={errors.email}
          required
        />

        <PasswordInput
          id="password"
          label="Password"
          placeholder="Password"
          value={formData.password}
          onChange={(e) => setFormData({ ...formData, password: e.target.value })}
          error={errors.password}
          required
        />

        <div className="flex items-center justify-between">
          <Link
            to="/forgot-password"
            className="text-sm font-medium text-primary hover:text-primary/90"
          >
            Forgot password?
          </Link>
        </div>

        {errors.submit && (
          <div className="text-sm text-red-600 text-center" role="alert">
            {errors.submit}
          </div>
        )}

        <AuthButton
          type="submit"
          loading={loginMutation.isPending}
          variant="primary"
        >
          Log in
        </AuthButton>
        </form>

        <div className="mt-6">
          <AuthDivider />
        </div>

        <div className="mt-6">
          <GoogleOAuthButton
            onClick={handleGoogleLogin}
            loading={isGoogleLoading}
            disabled={loginMutation.isPending || isGoogleLoading}
            label="Sign in with Google"
          />
        </div>

        <p className="mt-auto md:mt-6 text-center text-sm text-gray-600">
          Don't have an account?{' '}
          <Link to="/signup" className="font-medium text-primary hover:text-primary/90">
            Sign up
          </Link>
        </p>
      </div>
    </AuthForm>
  );
}

