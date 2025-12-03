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

import React, { useState, useEffect } from 'react';
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
  email: string;
  reason?: string;
  onNavigate: (screen: string, params?: UnknownInputParams) => void;
  onBack: () => void;
}

export const OtpScreen = ({ email, reason, onNavigate, onBack }: Props) => {
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [resendLoading, setResendLoading] = useState(false);
  const [timer, setTimer] = useState(30);
  const [focusedInput, setFocusedInput] = useState(false);
  const { verifyOtp, resendOtp } = useAuthContext();

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (timer > 0) {
      interval = setInterval(() => {
        setTimer((prev) => prev - 1);
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [timer]);

  const handleVerify = async () => {
    if (!otp) {
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: 'Please enter the code',
      });
      return;
    }

    setLoading(true);
    try {
      await verifyOtp(email, otp);
      
      if (reason === 'forgot-password') {
        onNavigate('reset-password', { email, otp });
      } else {
        // Email verification case
        Toast.show({
          type: 'success',
          text1: 'Verified',
          text2: 'Email verified successfully. Proceeding to dashboard.',
        });
        onNavigate('dashboard');
      }
    } catch {
      // Error handled in context
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    if (timer > 0 || resendLoading) return;
    
    setResendLoading(true);
    try {
      await resendOtp(email);
      setTimer(30);
    } catch {
      // Error handled in context
    } finally {
      setResendLoading(false);
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
            <Text style={styles.title}>Enter Code</Text>
            <Text style={styles.subtitle}>
              We sent a code to {email}. Please enter it below.
            </Text>
          </View>

          <View style={styles.form}>
            <TextInput
              style={[
                styles.input,
                focusedInput && styles.inputFocused,
              ]}
              placeholder="123456"
              placeholderTextColor={colors.text.placeholder}
              value={otp}
              onChangeText={setOtp}
              keyboardType="number-pad"
              maxLength={6}
              autoFocus
              onFocus={() => setFocusedInput(true)}
              onBlur={() => setFocusedInput(false)}
            />

            <TouchableOpacity
              style={styles.primaryButton}
              onPress={handleVerify}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryButtonText}>Verify</Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.resendButton}
              onPress={handleResend}
              disabled={timer > 0 || resendLoading || loading}
            >
              {resendLoading ? (
                 <ActivityIndicator size="small" color={colors.primary.main} />
              ) : (
                <Text style={[
                  styles.resendText,
                  (timer > 0) && styles.resendTextDisabled
                ]}>
                  {timer > 0 
                    ? `Resend code in ${timer}s`
                    : "Didn't receive code? Resend"}
                </Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity style={styles.footer} onPress={onBack}>
              <Text style={styles.footerLink}>Back</Text>
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
    fontSize: 24,
    color: colors.text.secondary,
    marginBottom: 16,
    textAlign: 'center',
    letterSpacing: 8,
  },
  inputFocused: {
    borderColor: colors.border.active,
    borderWidth: 2,
  },
  primaryButton: {
    backgroundColor: colors.primary.main,
    borderRadius: 24,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 8,
    shadowColor: colors.primary.shadow,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 4,
  },
  primaryButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '600',
  },
  resendButton: {
    marginTop: 24,
    alignItems: 'center',
    paddingVertical: 8,
  },
  resendText: {
    color: colors.primary.main,
    fontSize: 14,
    fontWeight: '500',
  },
  resendTextDisabled: {
    color: colors.text.tertiary,
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
