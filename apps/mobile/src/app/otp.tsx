import React from 'react';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { OtpScreen } from '../screens/OtpScreen';

type OtpParams = {
  email?: string | string[];
  reason?: string | string[];
};

export default function OtpRoute() {
  const router = useRouter();
  const { email, reason } = useLocalSearchParams<OtpParams>();

  const emailValue = Array.isArray(email) ? email[0] : email;
  const reasonValue = Array.isArray(reason) ? reason[0] : reason;

  return (
    <OtpScreen
      email={emailValue ?? ''}
      reason={reasonValue}
      onNavigate={(screen, params) => {
        if (screen === 'reset-password') {
          router.push({
            pathname: '/reset-password',
            params,
          });
        } else if (screen === 'login') {
          router.replace('/');
        } else if (screen === 'forgot-password') {
          router.replace('/forgot-password');
        }
      }}
      onBack={() => {
        if (reasonValue === 'signup-verification') {
          router.replace('/');
        } else {
          router.replace('/forgot-password');
        }
      }}
    />
  );
}


