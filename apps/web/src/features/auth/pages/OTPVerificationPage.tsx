/**
 * OTP Verification page component
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { AuthButton } from '../components/AuthButton';
import { OTPCodeInput } from '../components/OTPCodeInput';
import { useVerifyOTP } from '../hooks/useVerifyOTP';
import { authApi } from '../services/authApi';

export function OTPVerificationPage() {
  const verifyOTPMutation = useVerifyOTP();
  const [otp, setOtp] = useState('');
  const [email, setEmail] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [resendCooldown, setResendCooldown] = useState(0);
  const [isResending, setIsResending] = useState(false);
  const hasAutoResent = useRef(false);

  const handleResend = useCallback(async (emailToUse?: string) => {
    const emailForResend = emailToUse || email;
    if (resendCooldown > 0 || !emailForResend) return;

    setIsResending(true);
    try {
      await authApi.resendOTP(emailForResend);
      setResendCooldown(60); // 60 second cooldown
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

  useEffect(() => {
    // Get email from localStorage or state (set during signup)
    const signupEmail = localStorage.getItem('signup_email');
    if (signupEmail) {
      setEmail(signupEmail);
      
      // Auto-trigger resend if coming from login (inactive account) - only once
      const shouldAutoResend = localStorage.getItem('auto_resend_otp') === 'true';
      if (shouldAutoResend && !hasAutoResent.current) {
        hasAutoResent.current = true;
        localStorage.removeItem('auto_resend_otp');
        // Trigger resend with the email directly (don't wait for state update)
        handleResend(signupEmail);
      }
    }
  }, [handleResend]);

  useEffect(() => {
    if (resendCooldown > 0) {
      const timer = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [resendCooldown]);

  const handleVerify = async () => {
    if (otp.length !== 6) {
      setErrors({ otp: 'Please enter the complete 6-digit code' });
      return;
    }

    if (!email) {
      setErrors({ otp: 'Email not found. Please sign up again.' });
      return;
    }

    try {
      await verifyOTPMutation.mutateAsync({
        email,
        code: otp,
      });
    } catch (error: unknown) {
      const errorMessage = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setErrors({
        otp: errorMessage || 'Invalid code. Please try again.',
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
            Enter Verification Code
          </h1>
        </div>
        <p className="text-gray-600 text-center mb-8 md:-mt-6">
          We've sent a code to your email. Enter it below to verify.
        </p>

        <div className="space-y-6">
        <OTPCodeInput value={otp} onChange={setOtp} error={errors.otp} />

        {errors.resend && (
          <div className="text-sm text-red-600 text-center" role="alert">
            {errors.resend}
          </div>
        )}

        <AuthButton
          type="button"
          onClick={handleVerify}
          loading={verifyOTPMutation.isPending}
          variant="primary"
          disabled={otp.length !== 6}
        >
          Verify
        </AuthButton>

        <AuthButton
          type="button"
          onClick={() => handleResend()}
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

