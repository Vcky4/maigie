import React from 'react';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { ResetPasswordScreen } from '../screens/ResetPasswordScreen';

type ResetParams = {
  email?: string | string[];
  otp?: string | string[];
};

export default function ResetPasswordRoute() {
  const router = useRouter();
  const { email, otp } = useLocalSearchParams<ResetParams>();

  const emailValue = Array.isArray(email) ? email[0] : email;
  const otpValue = Array.isArray(otp) ? otp[0] : otp;

  return (
    <ResetPasswordScreen
      email={emailValue ?? ''}
      otp={otpValue ?? ''}
      onNavigate={(screen) => {
        if (screen === 'login') {
          router.replace('/');
        }
      }}
    />
  );
}


