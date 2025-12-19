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

import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthContext } from '../../../context/AuthContext';
import { colors } from '../../../lib/colors';

export default function Dashboard() {
  const router = useRouter();
  const { logout } = useAuthContext();

  const handleLogout = async () => {
    await logout();
    router.replace('/auth');
  };

  const handleSubscription = () => {
    router.push('/subscription');
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Maigie</Text>
      <Text style={styles.subtitle}>Your intelligent study companion</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Account</Text>
        <TouchableOpacity style={styles.primaryButton} onPress={handleSubscription}>
          <Text style={styles.primaryButtonText}>Manage Subscription</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.dangerButton} onPress={handleLogout}>
          <Text style={styles.dangerButtonText}>Logout</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    paddingTop: 48,
    backgroundColor: colors.background.secondary,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: colors.text.primary,
  },
  subtitle: {
    marginTop: 6,
    marginBottom: 18,
    color: colors.text.tertiary,
    fontSize: 14,
    fontWeight: '500',
  },
  card: {
    backgroundColor: colors.background.primary,
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: colors.border.default,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.text.primary,
    marginBottom: 12,
  },
  primaryButton: {
    backgroundColor: colors.primary.main,
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    marginBottom: 12,
  },
  primaryButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '700',
  },
  dangerButton: {
    backgroundColor: colors.status.danger,
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  dangerButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '700',
  },
});


