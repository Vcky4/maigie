import React from 'react';
import { useRouter } from 'expo-router';
import { ForgotPasswordScreen } from '../screens/ForgotPasswordScreen';

export default function ForgotPasswordRoute() {
  const router = useRouter();

  return (
    <ForgotPasswordScreen
      onNavigate={(screen, params) => {
        if (screen === 'otp') {
          router.push({
            pathname: '/otp',
            params,
          });
        } else if (screen === 'login') {
          router.replace('/');
        }
      }}
      onBack={() => router.back()}
    />
  );
}


