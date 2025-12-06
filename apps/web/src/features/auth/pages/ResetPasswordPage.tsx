/**
 * Reset Password page component
 */

import { useState, FormEvent, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { PasswordInput } from '../components/PasswordInput';
import { AuthButton } from '../components/AuthButton';
import { useResetPassword } from '../hooks/useResetPassword';

export function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const resetPasswordMutation = useResetPassword();

  const { email, code } = (location.state as { email?: string; code?: string }) || {};

  const [formData, setFormData] = useState({
    newPassword: '',
    confirmPassword: '',
  });

  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!email || !code) {
      navigate('/forgot-password');
    }
  }, [email, code, navigate]);

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.newPassword) {
      newErrors.newPassword = 'Password is required';
    } else if (formData.newPassword.length < 8) {
      newErrors.newPassword = 'Password must be at least 8 characters';
    }

    if (!formData.confirmPassword) {
      newErrors.confirmPassword = 'Please confirm your password';
    } else if (formData.newPassword !== formData.confirmPassword) {
      newErrors.confirmPassword = 'Passwords do not match';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!email || !code) {
      setErrors({ submit: 'Invalid reset code. Please request a new one.' });
      return;
    }

    if (!validateForm()) {
      return;
    }

    try {
      await resetPasswordMutation.mutateAsync({
        email,
        code,
        newPassword: formData.newPassword,
        confirmPassword: formData.confirmPassword,
      });
    } catch (error: unknown) {
      const errorMessage = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setErrors({
        submit: errorMessage || 'Failed to reset password. Please try again.',
      });
    }
  };

  if (!email || !code) {
    return null;
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
          Enter a new password for your account.
        </p>

        <form onSubmit={handleSubmit} className="space-y-5">
        <PasswordInput
          id="newPassword"
          label="New password"
          placeholder="New password"
          value={formData.newPassword}
          onChange={(e) =>
            setFormData({ ...formData, newPassword: e.target.value })
          }
          error={errors.newPassword}
          required
        />

        <PasswordInput
          id="confirmPassword"
          label="Confirm password"
          placeholder="Confirm password"
          value={formData.confirmPassword}
          onChange={(e) =>
            setFormData({ ...formData, confirmPassword: e.target.value })
          }
          error={errors.confirmPassword}
          required
        />

        {errors.submit && (
          <div className="text-sm text-red-600 text-center" role="alert">
            {errors.submit}
          </div>
        )}

        <AuthButton
          type="submit"
          loading={resetPasswordMutation.isPending}
          variant="primary"
        >
          Set New Password
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

