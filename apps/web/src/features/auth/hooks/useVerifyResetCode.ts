/**
 * Hook for verifying reset code
 */

import { useMutation } from '@tanstack/react-query';
import { authApi } from '../services/authApi';
import type { VerifyResetCodeRequest } from '../types/auth.types';

export function useVerifyResetCode() {
  return useMutation({
    mutationFn: (data: VerifyResetCodeRequest) => authApi.verifyResetCode(data),
  });
}

