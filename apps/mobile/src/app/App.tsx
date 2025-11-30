import React from 'react';
import { StatusBar } from 'react-native';
import { AuthNavigator } from './navigation/AuthNavigator';
import { AuthProvider } from './context/AuthContext';
import Toast from 'react-native-toast-message';

export const App = () => {
  return (
    <AuthProvider>
      <StatusBar barStyle="dark-content" backgroundColor="#FFFFFF" />
      <AuthNavigator />
      <Toast />
    </AuthProvider>
  );
};

export default App;
