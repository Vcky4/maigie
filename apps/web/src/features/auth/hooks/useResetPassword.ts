/**
 * Hook for reset password
 */

import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import type { PasswordReset } from '../types/auth.types';

export function useResetPassword() {
  const navigate = useNavigate();

  return useMutation({
    mutationFn: (data: PasswordReset) => authApi.resetPassword(data),
    onSuccess: () => {
      navigate('/login');
    },
  });
}

