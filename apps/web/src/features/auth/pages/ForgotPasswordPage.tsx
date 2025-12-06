/**
 * Forgot Password page component
 */

import { useState, FormEvent, useCallback, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { AuthInput } from '../components/AuthInput';
import { AuthButton } from '../components/AuthButton';
import { OTPCodeInput } from '../components/OTPCodeInput';
import { AuthDivider } from '../components/AuthDivider';
import { useForgotPassword } from '../hooks/useForgotPassword';
import { useVerifyResetCode } from '../hooks/useVerifyResetCode';
import { authApi } from '../services/authApi';

export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const forgotPasswordMutation = useForgotPassword();
  const verifyResetCodeMutation = useVerifyResetCode();
  
  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [resendCooldown, setResendCooldown] = useState(0);
  const [isResending, setIsResending] = useState(false);

  useEffect(() => {
    if (resendCooldown > 0) {
      const timer = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [resendCooldown]);

  const validateEmail = () => {
    const newErrors: Record<string, string> = {};

    if (!email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      newErrors.email = 'Please enter a valid email address';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleEmailSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!validateEmail()) {
      return;
    }

    try {
      await forgotPasswordMutation.mutateAsync({ email });
      setStep('code');
      setResendCooldown(60); // 60 second cooldown
      setErrors({});
    } catch (error: unknown) {
      // Still show code step to prevent email enumeration
      setStep('code');
      setResendCooldown(60);
      setErrors({});
    }
  };

  const handleResend = useCallback(async () => {
    if (resendCooldown > 0 || !email) return;

    setIsResending(true);
    try {
      await authApi.forgotPassword({ email });
      setResendCooldown(60);
      setErrors({});
    } catch (error: unknown) {
      const errorMessage = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setErrors({
        resend: errorMessage || 'Failed to resend code. Please try again.',
      });
    } finally {
      setIsResending(false);
    }
  }, [email, resendCooldown]);

  const handleCodeSubmit = async () => {
    if (resetCode.length !== 6) {
      setErrors({ code: 'Please enter the complete 6-digit code' });
      return;
    }

    if (!email) {
      setErrors({ code: 'Email not found. Please start over.' });
      return;
    }

    try {
      await verifyResetCodeMutation.mutateAsync({
        email,
        code: resetCode,
      });
      
      // Navigate to reset password page with email and code
      navigate('/reset-password', {
        state: { email, code: resetCode },
      });
    } catch (error: unknown) {
      const errorMessage = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setErrors({
        code: errorMessage || 'Invalid code. Please try again.',
      });
    }
  };

  if (step === 'code') {
    return (
      <AuthForm>
        <div className="flex flex-col flex-1">
          <div className="flex flex-col mb-6 md:items-center">
            <div className="pb-3 -mx-4 -mt-4 px-4 pt-4 border-b border-gray-200 md:mx-0 md:px-0 md:mt-0 md:pt-0 md:border-b-0 md:pb-0">
              <AuthLogo />
            </div>
            <h1 className="text-3xl font-semibold text-charcoal mt-8 md:text-center">
              Enter Reset Code
            </h1>
          </div>
          <p className="text-gray-600 text-center mb-8 md:-mt-6">
            We've sent a reset code to {email}. Enter it below to continue.
          </p>

          <div className="space-y-6">
            <OTPCodeInput value={resetCode} onChange={setResetCode} error={errors.code} />

            {errors.resend && (
              <div className="text-sm text-red-600 text-center" role="alert">
                {errors.resend}
              </div>
            )}

            <AuthButton
              type="button"
              onClick={handleCodeSubmit}
              loading={verifyResetCodeMutation.isPending}
              variant="primary"
              disabled={resetCode.length !== 6}
            >
              Verify Code
            </AuthButton>

            <AuthDivider />

            <AuthButton
              type="button"
              onClick={handleResend}
              loading={isResending}
              variant="secondary"
              disabled={resendCooldown > 0}
            >
              {resendCooldown > 0
                ? `Resend Code (${resendCooldown}s)`
                : 'Resend Code'}
            </AuthButton>
          </div>

          <p className="mt-auto md:mt-6 text-center text-sm text-gray-600">
            <Link to="/login" className="font-medium text-primary hover:text-primary/90">
              Back to Login
            </Link>
          </p>
        </div>
      </AuthForm>
    );
  }

  return (
    <AuthForm>
      <div className="flex flex-col flex-1">
        <div className="flex flex-col mb-6 md:items-center">
          <div className="pb-3 -mx-4 -mt-4 px-4 pt-4 border-b border-gray-200 md:mx-0 md:px-0 md:mt-0 md:pt-0 md:border-b-0 md:pb-0">
            <AuthLogo />
          </div>
          <h1 className="text-3xl font-semibold text-charcoal mt-8 md:text-center">
            Reset Password
          </h1>
        </div>
        <p className="text-gray-600 text-center mb-8 md:-mt-6">
          Enter your email address and we'll send you a reset code.
        </p>

        <form onSubmit={handleEmailSubmit} className="space-y-5">
          <AuthInput
            id="email"
            label="Email address"
            type="email"
            placeholder="Email address"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={errors.email}
            required
          />

          {errors.submit && (
            <div className="text-sm text-red-600 text-center" role="alert">
              {errors.submit}
            </div>
          )}

          <AuthButton
            type="submit"
            loading={forgotPasswordMutation.isPending}
            variant="primary"
          >
            Send Reset Code
          </AuthButton>
        </form>

        <p className="mt-auto md:mt-6 text-center text-sm text-gray-600">
          <Link to="/login" className="font-medium text-primary hover:text-primary/90">
            Back to Login
          </Link>
        </p>
      </div>
    </AuthForm>
  );
}

