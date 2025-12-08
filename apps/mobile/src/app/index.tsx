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

import { useEffect, useRef } from 'react';
import { useRouter } from 'expo-router';
import { useAuthContext } from '../context/AuthContext';

export default function Index() {
  const router = useRouter();
  const { userToken, isLoading } = useAuthContext();
  const hasNavigated = useRef(false);

  useEffect(() => {
    // Wait for auth state to be loaded and ensure we only navigate once
    if (isLoading || hasNavigated.current) return;

    // Use requestAnimationFrame to ensure router is mounted
    const frameId = requestAnimationFrame(() => {
      try {
        hasNavigated.current = true;
        if (userToken) {
          router.replace('/dashboard');
        } else {
          router.replace('/auth');
        }
      } catch {
        // Router might not be ready yet, retry after a short delay
        hasNavigated.current = false;
        setTimeout(() => {
          if (!hasNavigated.current) {
            hasNavigated.current = true;
            if (userToken) {
              router.replace('/dashboard');
            } else {
              router.replace('/auth');
            }
          }
        }, 100);
      }
    });

    return () => cancelAnimationFrame(frameId);
  }, [userToken, isLoading, router]);

  return null;
}


