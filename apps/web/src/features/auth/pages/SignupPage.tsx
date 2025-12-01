/**
 * Signup page component
 */

import { useState, FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AuthForm } from '../components/AuthForm';
import { AuthLogo } from '../components/AuthLogo';
import { AuthInput } from '../components/AuthInput';
import { PasswordInput } from '../components/PasswordInput';
import { AuthButton } from '../components/AuthButton';
import { GoogleOAuthButton } from '../components/GoogleOAuthButton';
import { AuthDivider } from '../components/AuthDivider';
import { useSignup } from '../hooks/useSignup';

export function SignupPage() {
  const navigate = useNavigate();
  const signupMutation = useSignup();

  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
  });

  const [errors, setErrors] = useState<Record<string, string>>({});

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.name.trim()) {
      newErrors.name = 'Full name is required';
    }

    if (!formData.email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) {
      newErrors.email = 'Please enter a valid email address';
    }

    if (!formData.password) {
      newErrors.password = 'Password is required';
    } else if (formData.password.length < 8) {
      newErrors.password = 'Password must be at least 8 characters';
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
      // Store email for OTP verification
      localStorage.setItem('signup_email', formData.email);
      await signupMutation.mutateAsync({
        email: formData.email,
        password: formData.password,
        name: formData.name,
      });
    } catch (error: any) {
      setErrors({
        submit: error?.response?.data?.detail || 'An error occurred. Please try again.',
      });
    }
  };

  const handleGoogleSignup = () => {
    // TODO: Implement Google OAuth
    console.log('Google signup clicked');
  };

  return (
    <AuthForm>
      <div className="flex flex-col flex-1">
        <div className="flex flex-col mb-6 md:items-center">
          <div className="pb-3 -mx-4 -mt-4 px-4 pt-4 border-b border-gray-200 md:mx-0 md:px-0 md:mt-0 md:pt-0 md:border-b-0 md:pb-0">
            <AuthLogo />
          </div>
          <h1 className="text-3xl font-semibold text-charcoal mt-8 md:text-center">
            Create your account
          </h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-2">
          <AuthInput
            id="name"
            type="text"
            placeholder="Full Name"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            error={errors.name}
            required
          />

          <AuthInput
            id="email"
            type="email"
            placeholder="Email address"
            value={formData.email}
            onChange={(e) => setFormData({ ...formData, email: e.target.value })}
            error={errors.email}
            required
          />

          <PasswordInput
            id="password"
            placeholder="Password"
            value={formData.password}
            onChange={(e) => setFormData({ ...formData, password: e.target.value })}
            error={errors.password}
            required
          />

          {errors.submit && (
            <div className="text-sm text-red-600 text-center" role="alert">
              {errors.submit}
            </div>
          )}

          <div className="pt-2">
            <AuthButton
              type="submit"
              loading={signupMutation.isPending}
              variant="primary"
            >
              Sign Up
            </AuthButton>
          </div>
        </form>

        <div className="mt-4">
          <AuthDivider />
        </div>

        <div className="mt-4">
          <GoogleOAuthButton onClick={handleGoogleSignup} />
        </div>
      </div>

      <p className="mt-auto md:mt-5 text-center text-sm text-gray-600">
        Already have an account?{' '}
        <Link to="/login" className="font-medium text-primary hover:text-primary/90">
          Log in
        </Link>
      </p>
    </AuthForm>
  );
}

