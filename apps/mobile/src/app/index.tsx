import React from 'react';
import { useRouter } from 'expo-router';
import { AuthScreen } from '../screens/AuthScreen';

export default function Index() {
  const router = useRouter();

  return (
    <AuthScreen
      onForgotPassword={() => router.push('/forgot-password')}
      onSignupSuccess={(email) =>
        router.push({
          pathname: '/otp',
          params: { email, reason: 'signup-verification' },
        })
      }
    />
  );
}


