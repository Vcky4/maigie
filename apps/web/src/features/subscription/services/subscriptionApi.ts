import axios from 'axios';
import { useAuthStore } from '../../auth/store/authStore';

// Get API URL from environment variables
const API_URL = import.meta.env.VITE_API_URL || 'https://pr-67-api-preview.maigie.com/api/v1';

// Create axios instance
const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token interceptor
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export interface CheckoutSessionResponse {
  session_id: string;
  url: string | null;
  modified?: boolean;
  is_upgrade?: boolean | null;
  current_period_end?: string | null;
}

export interface PortalSessionResponse {
  url: string;
}

export interface CancelSubscriptionResponse {
  status: string;
  cancel_at_period_end: boolean;
  current_period_end: string;
}

export const subscriptionApi = {
  createCheckoutSession: async (priceType: 'monthly' | 'yearly'): Promise<CheckoutSessionResponse> => {
    const response = await api.post<CheckoutSessionResponse>('/subscriptions/checkout', {
      price_type: priceType,
    });
    return response.data;
  },

  createPortalSession: async (): Promise<PortalSessionResponse> => {
    const response = await api.post<PortalSessionResponse>('/subscriptions/portal');
    return response.data;
  },

  cancelSubscription: async (): Promise<CancelSubscriptionResponse> => {
    const response = await api.post<CancelSubscriptionResponse>('/subscriptions/cancel');
    return response.data;
  },
};

