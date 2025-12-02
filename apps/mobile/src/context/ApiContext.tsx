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

import React, { createContext, useContext, ReactNode, useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { apiRequest } from '../utils/api';

export interface ApiRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  headers?: Record<string, string>;
  body?: any;
  requiresAuth?: boolean; // Default: true, set to false for public endpoints
}

export interface ApiContextType {
  get: <T = any>(endpoint: string, options?: Omit<ApiRequestOptions, 'method'>) => Promise<T>;
  post: <T = any>(endpoint: string, body?: any, options?: Omit<ApiRequestOptions, 'method' | 'body'>) => Promise<T>;
  put: <T = any>(endpoint: string, body?: any, options?: Omit<ApiRequestOptions, 'method' | 'body'>) => Promise<T>;
  patch: <T = any>(endpoint: string, body?: any, options?: Omit<ApiRequestOptions, 'method' | 'body'>) => Promise<T>;
  delete: <T = any>(endpoint: string, options?: Omit<ApiRequestOptions, 'method'>) => Promise<T>;
  request: <T = any>(endpoint: string, options?: ApiRequestOptions) => Promise<T>;
  // Token management methods
  getToken: () => string | null;
  setToken: (token: string | null) => Promise<void>;
  userToken: string | null;
}

const ApiContext = createContext<ApiContextType | null>(null);

export const ApiProvider = ({ children }: { children: ReactNode }) => {
  const [userToken, setUserTokenState] = useState<string | null>(null);

  // Load token from AsyncStorage on mount
  useEffect(() => {
    const loadToken = async () => {
      try {
        const token = await AsyncStorage.getItem('userToken');
        setUserTokenState(token);
      } catch (error) {
        console.error('Failed to load token from storage:', error);
      }
    };
    loadToken();
  }, []);

  const setToken = async (token: string | null) => {
    try {
      if (token) {
        await AsyncStorage.setItem('userToken', token);
      } else {
        await AsyncStorage.removeItem('userToken');
      }
      setUserTokenState(token);
    } catch (error) {
      console.error('Failed to save token to storage:', error);
      throw error;
    }
  };

  const getToken = () => userToken;

  const request = async <T = any>(
    endpoint: string,
    options: ApiRequestOptions = {}
  ): Promise<T> => {
    const {
      requiresAuth = true,
      ...restOptions
    } = options;

    return apiRequest<T>(endpoint, {
      ...restOptions,
      token: requiresAuth ? userToken : null,
    });
  };

  const get = <T = any>(
    endpoint: string,
    options?: Omit<ApiRequestOptions, 'method'>
  ): Promise<T> => {
    return request<T>(endpoint, { ...options, method: 'GET' });
  };

  const post = <T = any>(
    endpoint: string,
    body?: any,
    options?: Omit<ApiRequestOptions, 'method' | 'body'>
  ): Promise<T> => {
    return request<T>(endpoint, { ...options, method: 'POST', body });
  };

  const put = <T = any>(
    endpoint: string,
    body?: any,
    options?: Omit<ApiRequestOptions, 'method' | 'body'>
  ): Promise<T> => {
    return request<T>(endpoint, { ...options, method: 'PUT', body });
  };

  const patch = <T = any>(
    endpoint: string,
    body?: any,
    options?: Omit<ApiRequestOptions, 'method' | 'body'>
  ): Promise<T> => {
    return request<T>(endpoint, { ...options, method: 'PATCH', body });
  };

  const deleteMethod = <T = any>(
    endpoint: string,
    options?: Omit<ApiRequestOptions, 'method'>
  ): Promise<T> => {
    return request<T>(endpoint, { ...options, method: 'DELETE' });
  };

  return (
    <ApiContext.Provider
      value={{
        get,
        post,
        put,
        patch,
        delete: deleteMethod,
        request,
        getToken,
        setToken,
        userToken,
      }}
    >
      {children}
    </ApiContext.Provider>
  );
};

export const useApi = () => {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApi must be used within an ApiProvider');
  }
  return context;
};
