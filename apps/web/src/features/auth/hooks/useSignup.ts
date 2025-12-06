/**
 * Hook for user signup
 */

import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import type { UserSignup } from '../types/auth.types';

export function useSignup() {
  const navigate = useNavigate();

  return useMutation({
    mutationFn: (data: UserSignup) => authApi.signup(data),
    onSuccess: () => {
      // Redirect to OTP verification page after successful signup
      navigate('/verify-otp');
    },
  });
}

