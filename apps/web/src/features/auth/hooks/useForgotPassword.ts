/**
 * Hook for forgot password
 */

import { useMutation } from '@tanstack/react-query';
import { authApi } from '../services/authApi';
import type { PasswordResetRequest } from '../types/auth.types';

export function useForgotPassword() {
  return useMutation({
    mutationFn: (data: PasswordResetRequest) => authApi.forgotPassword(data),
  });
}

