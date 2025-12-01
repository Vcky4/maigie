import React, { createContext, useState, useEffect, ReactNode, useContext } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import Toast from 'react-native-toast-message';

// Backend URL - adjust as needed
const API_URL = Platform.select({
  android: 'http://10.0.2.2:8000',
  ios: 'http://localhost:8000',
  default: 'http://localhost:8000',
});

interface AuthContextType {
  userToken: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<void>;
  logout: () => Promise<void>;
  forgotPassword: (email: string) => Promise<void>;
  verifyOtp: (email: string, otp: string) => Promise<void>;
  resetPassword: (email: string, otp: string, password: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [isLoading, setIsLoading] = useState(true);
  const [userToken, setUserToken] = useState<string | null>(null);

  const login = async (email: string, password: string) => {
    setIsLoading(true);
    try {
      const response = await fetch(`${API_URL}/login/json`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Login failed');
      }

      const token = data.access_token;
      setUserToken(token);
      await AsyncStorage.setItem('userToken', token);
      
      Toast.show({
        type: 'success',
        text1: 'Welcome back!',
        text2: 'Logged in successfully',
      });
    } catch (error: any) {
      Toast.show({
        type: 'error',
        text1: 'Login Failed',
        text2: error.message,
      });
      throw error; // Re-throw to let UI handle if needed
    } finally {
      setIsLoading(false);
    }
  };

  const signup = async (email: string, password: string, name: string) => {
    setIsLoading(true);
    try {
      const response = await fetch(`${API_URL}/signup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password, name }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Signup failed');
      }

      Toast.show({
        type: 'success',
        text1: 'Account Created',
        text2: 'Please log in with your new account',
      });
      
      // Optional: Auto-login after signup could go here if backend returned a token
    } catch (error: any) {
      Toast.show({
        type: 'error',
        text1: 'Signup Failed',
        text2: error.message,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    setIsLoading(true);
    try {
      await AsyncStorage.removeItem('userToken');
      setUserToken(null);
      Toast.show({
        type: 'info',
        text1: 'Logged out',
      });
    } catch (error) {
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  const forgotPassword = async (email: string) => {
    setIsLoading(true);
    try {
      // Mock API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      Toast.show({
        type: 'success',
        text1: 'Code Sent',
        text2: `Reset code sent to ${email}`,
      });
    } catch (error: any) {
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: error.message,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const verifyOtp = async (email: string, otp: string) => {
    setIsLoading(true);
    try {
      // Mock API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (otp !== '123456') { // Mock verification
         throw new Error('Invalid Code');
      }
      Toast.show({
        type: 'success',
        text1: 'Verified',
        text2: 'Code verified successfully',
      });
    } catch (error: any) {
      Toast.show({
        type: 'error',
        text1: 'Verification Failed',
        text2: error.message,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const resetPassword = async (email: string, otp: string, password: string) => {
    setIsLoading(true);
    try {
      // Mock API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      Toast.show({
        type: 'success',
        text1: 'Password Reset',
        text2: 'Your password has been updated',
      });
    } catch (error: any) {
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: error.message,
      });
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const isLoggedIn = async () => {
    try {
      setIsLoading(true);
      const token = await AsyncStorage.getItem('userToken');
      setUserToken(token);
    } catch (error) {
      console.error(`isLoggedIn error: ${error}`);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    isLoggedIn();
  }, []);

  return (
    <AuthContext.Provider value={{ 
      login, 
      logout, 
      signup, 
      forgotPassword, 
      verifyOtp, 
      resetPassword,
      isLoading, 
      userToken 
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuthContext = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthContext must be used within an AuthProvider');
  }
  return context;
};
