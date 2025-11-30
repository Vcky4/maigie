import React from 'react';
import { StatusBar } from 'react-native';
import { AuthScreen } from './screens/AuthScreen';

export const App = () => {
  return (
    <>
      <StatusBar barStyle="dark-content" backgroundColor="#FFFFFF" />
      <AuthScreen />
    </>
  );
};

export default App;
