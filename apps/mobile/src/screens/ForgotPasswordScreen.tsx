/*
 * Maigie - Your Intelligent Study Companion
 * Copyright (C) 2025 Maigie
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  ActivityIndicator,
  Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuthContext } from '../context/AuthContext';
import Toast from 'react-native-toast-message';
import { colors } from '../lib/colors';
import { UnknownInputParams } from 'expo-router';

interface Props {
  onNavigate: (screen: string, params?: UnknownInputParams) => void;
  onBack: () => void;
}

export const ForgotPasswordScreen = ({ onNavigate, onBack }: Props) => {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [focusedInput, setFocusedInput] = useState<string | null>(null);
  const { forgotPassword } = useAuthContext();

  const handleSend = async () => {
    if (!email) {
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: 'Please enter your email',
      });
      return;
    }

    setLoading(true);
    try {
      await forgotPassword(email);
      onNavigate('otp', { email, reason: 'forgot-password' });
    } catch {
      // Error handled in context
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{ flex: 1 }}
      >
        <ScrollView contentContainerStyle={styles.scrollContent}>
          <View style={styles.header}>
            <View style={styles.logoContainer}>
               <Image 
                source={require('../../assets/images/icon.png')} 
                style={styles.logo}
                resizeMode="contain"
              />
              <Text style={styles.appName}>Maigie</Text>
            </View>
            <Text style={styles.title}>Forgot Password?</Text>
            <Text style={styles.subtitle}>
              Enter your email address and we'll send you a code to reset your password.
            </Text>
          </View>

          <View style={styles.form}>
            <TextInput
              style={[
                styles.input,
                focusedInput === 'email' && styles.inputFocused,
              ]}
              placeholder="Email address"
              placeholderTextColor={colors.text.placeholder}
              value={email}
              onChangeText={setEmail}
              keyboardType="email-address"
              autoCapitalize="none"
              onFocus={() => setFocusedInput('email')}
              onBlur={() => setFocusedInput(null)}
            />

            <TouchableOpacity
              style={styles.primaryButton}
              onPress={handleSend}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryButtonText}>Send Code</Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity style={styles.footer} onPress={onBack}>
              <Text style={styles.footerLink}>Back to Log In</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background.primary,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingHorizontal: 24,
    paddingVertical: 40,
  },
  header: {
    alignItems: 'center',
    marginBottom: 32,
  },
  logoContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 24,
  },
  logo: {
    width: 32,
    height: 32,
    marginRight: 8,
  },
  appName: {
    fontSize: 24,
    fontWeight: '600',
    color: colors.text.secondary,
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    color: colors.text.primary,
    textAlign: 'center',
    marginBottom: 12,
  },
  subtitle: {
    fontSize: 16,
    color: colors.text.tertiary,
    lineHeight: 24,
    textAlign: 'center',
  },
  form: {
    width: '100%',
  },
  input: {
    backgroundColor: colors.background.input,
    borderWidth: 1,
    borderColor: colors.border.default,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text.secondary,
    marginBottom: 16,
  },
  inputFocused: {
    borderColor: colors.border.active,
    borderWidth: 2,
  },
  primaryButton: {
    backgroundColor: '#4F46E5',
    borderRadius: 24,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 8,
    shadowColor: '#4F46E5',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 4,
  },
  primaryButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  footer: {
    alignItems: 'center',
    marginTop: 24,
  },
  footerLink: {
    color: colors.primary.main,
    fontWeight: '600',
    fontSize: 16,
  },
});
