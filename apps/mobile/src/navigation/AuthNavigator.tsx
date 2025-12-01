import React, { useState } from 'react';
import { AuthScreen } from '../screens/AuthScreen';
import { ForgotPasswordScreen } from '../screens/ForgotPasswordScreen';
import { OtpScreen } from '../screens/OtpScreen';
import { ResetPasswordScreen } from '../screens/ResetPasswordScreen';

export const AuthNavigator = () => {
  const [currentScreen, setCurrentScreen] = useState('login');
  const [params, setParams] = useState<any>({});

  const handleNavigate = (screen: string, newParams?: any) => {
    setCurrentScreen(screen);
    if (newParams) {
      setParams({ ...params, ...newParams });
    }
  };

  switch (currentScreen) {
    case 'login':
      return (
        <AuthScreen
          onForgotPassword={() => handleNavigate('forgot-password')}
          onSignupSuccess={(email) => handleNavigate('otp', { email, reason: 'signup-verification' })}
        />
      );
    case 'forgot-password':
      return (
        <ForgotPasswordScreen
          onNavigate={handleNavigate}
          onBack={() => handleNavigate('login')}
        />
      );
    case 'otp':
      return (
        <OtpScreen
          email={params.email}
          reason={params.reason}
          onNavigate={handleNavigate}
          onBack={() => handleNavigate(params.reason === 'signup-verification' ? 'login' : 'forgot-password')}
        />
      );
    case 'reset-password':
      return (
        <ResetPasswordScreen
          email={params.email}
          otp={params.otp}
          onNavigate={handleNavigate}
        />
      );
    default:
      return <AuthScreen
        onForgotPassword={() => handleNavigate('forgot-password')}
        onSignupSuccess={(email) => handleNavigate('otp', { email, reason: 'signup-verification' })}
      />;

  }
};
