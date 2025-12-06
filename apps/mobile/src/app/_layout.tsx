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
import { StatusBar } from 'react-native';
import { Stack } from 'expo-router';
import Toast from 'react-native-toast-message';
import { ApiProvider } from '../context/ApiContext';
import { AuthProvider } from '../context/AuthContext';

export default function RootLayout() {
  return (
    <ApiProvider>
      <AuthProvider>
        <StatusBar barStyle="dark-content" backgroundColor="#FFFFFF" />
        <Stack screenOptions={{ headerShown: false }} />
        <Toast />
      </AuthProvider>
    </ApiProvider>
  );
}


