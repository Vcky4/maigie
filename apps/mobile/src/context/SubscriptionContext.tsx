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

import React, { createContext, useContext, ReactNode } from 'react';
import { useApi } from './ApiContext';
import { endpoints } from '../lib/endpoints';
import { CancelSubscriptionResponse, CheckoutSessionResponse, PortalSessionResponse, SubscriptionContextType } from '../lib/types';


const SubscriptionContext = createContext<SubscriptionContextType | null>(null);

export const SubscriptionProvider = ({ children }: { children: ReactNode }) => {
  const api = useApi();

  const createCheckoutSession = async (
    priceType: 'monthly' | 'yearly'
  ): Promise<CheckoutSessionResponse> => {
    return api.post<CheckoutSessionResponse>(
      endpoints.subscriptions.checkout,
      { price_type: priceType }
    );
  };

  const createPortalSession = async (): Promise<PortalSessionResponse> => {
    return api.post<PortalSessionResponse>(endpoints.subscriptions.portal);
  };

  const cancelSubscription = async (): Promise<CancelSubscriptionResponse> => {
    return api.post<CancelSubscriptionResponse>(endpoints.subscriptions.cancel);
  };

  return (
    <SubscriptionContext.Provider
      value={{
        createCheckoutSession,
        createPortalSession,
        cancelSubscription,
      }}
    >
      {children}
    </SubscriptionContext.Provider>
  );
};

export const useSubscriptionContext = () => {
  const context = useContext(SubscriptionContext);
  if (!context) {
    throw new Error('useSubscriptionContext must be used within a SubscriptionProvider');
  }
  return context;
};
