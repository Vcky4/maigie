/**
 * Forgot Password page component
 */

import { useState, FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { AuthInput } from '../components/AuthInput';
import { AuthButton } from '../components/AuthButton';
import { useForgotPassword } from '../hooks/useForgotPassword';

export function ForgotPasswordPage() {
  const forgotPasswordMutation = useForgotPassword();
  const [email, setEmail] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSuccess, setIsSuccess] = useState(false);

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (!email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      newErrors.email = 'Please enter a valid email address';
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
      await forgotPasswordMutation.mutateAsync({ email });
      setIsSuccess(true);
      setErrors({});
    } catch (error: any) {
      // Still show success message to prevent email enumeration
      setIsSuccess(true);
      setErrors({});
    }
  };

  if (isSuccess) {
    return (
      <AuthForm>
        <AuthLogo />
        <h1 className="text-3xl font-bold text-gray-900 mb-2 text-center">
          Check your email
        </h1>
        <p className="text-gray-600 text-center mb-8">
          If an account exists with that email, we've sent you a password reset link.
        </p>
        <Link to="/login">
          <AuthButton variant="primary" className="w-full">
            Back to Login
          </AuthButton>
        </Link>
      </AuthForm>
    );
  }

  return (
      <AuthForm>
        <AuthLogo />
        <h1 className="text-3xl font-bold text-gray-900 mb-2 text-center">
          Reset Password
        </h1>
        <p className="text-gray-600 text-center mb-8">
          Enter your email address and we'll send you a link to reset your password.
        </p>

      <form onSubmit={handleSubmit} className="space-y-5">
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
          Send Reset Link
        </AuthButton>
      </form>

      <p className="mt-6 text-center text-sm text-gray-600">
        <Link to="/login" className="font-medium text-primary hover:text-primary/90">
          Back to Login
        </Link>
      </p>
    </AuthForm>
  );
}

