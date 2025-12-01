import { useState } from 'react';
import Toast from 'react-native-toast-message';
import { useAuthContext } from '../context/AuthContext';

export const useAuth = () => {
  const { login, signup, isLoading } = useAuthContext();
  
  const [isSignUp, setIsSignUp] = useState(false);
  
  // Form State
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const handleAuth = async (onSignupSuccess?: (email: string) => void) => {
    if (!email || !password || (isSignUp && !name)) {
      Toast.show({
        type: 'error',
        text1: 'Validation Error',
        text2: 'Please fill in all fields',
      });
      return;
    }

    try {
      if (isSignUp) {
        await signup(email, password, name);
        if (onSignupSuccess) {
          onSignupSuccess(email);
        } else {
          setIsSignUp(false);
        }
      } else {
        await login(email, password);
      }
    } catch (error) {
      // Error handled in context
    }
  };

  return {
    isSignUp,
    setIsSignUp,
    loading: isLoading,
    name,
    setName,
    email,
    setEmail,
    password,
    setPassword,
    handleAuth,
  };
};
