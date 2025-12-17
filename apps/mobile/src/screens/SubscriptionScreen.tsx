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
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
// eslint-disable-next-line @nx/enforce-module-boundaries
import * as WebBrowser from 'expo-web-browser';
// eslint-disable-next-line @nx/enforce-module-boundaries
import * as Linking from 'expo-linking';
// eslint-disable-next-line @nx/enforce-module-boundaries
import Toast from 'react-native-toast-message';
// eslint-disable-next-line @nx/enforce-module-boundaries
import { Check } from 'lucide-react-native';
import { useSubscriptionContext } from '../context/SubscriptionContext';
import { useAuthContext } from '../context/AuthContext';
import { colors } from '../lib/colors';

WebBrowser.maybeCompleteAuthSession();

export default function SubscriptionScreen() {
  const { createCheckoutSession, createPortalSession, cancelSubscription } = useSubscriptionContext();
  const [loading, setLoading] = useState(false);
  const { user, fetchUser } = useAuthContext();
  const [loadingUser, setLoadingUser] = useState(true);

  useEffect(() => {
    fetchUser();
    setLoadingUser(false);
    // Listen for deep links when returning from Stripe (fallback)
    const subscription = Linking.addEventListener('url', handleDeepLink);
    return () => {
      subscription.remove();
    };
  }, []);


  const handleDeepLink = async (event: { url: string }) => {
    const { queryParams } = Linking.parse(event.url);
    if (queryParams?.session_id) {
      // Subscription successful, reload user data
      await fetchUser();
      setLoadingUser(false);
      Toast.show({
        type: 'success',
        text1: 'Subscription Updated',
        text2: 'Your subscription has been updated successfully',
      });
    }
  };

  const handleSubscribe = async (priceType: 'monthly' | 'yearly') => {
    setLoading(true);
    try {
      const session = await createCheckoutSession(priceType);

      // If subscription was modified directly (upgrade/downgrade)
      if (session.modified) {
        await fetchUser();
        Toast.show({
          type: 'success',
          text1: 'Subscription Updated',
          text2: session.is_upgrade
            ? 'Your subscription has been upgraded'
            : 'Your subscription has been updated',
        });
        return;
      }

      // If we have a URL, open it in browser
      if (session.url) {
        // Use openAuthSessionAsync to automatically close when redirected back
        // We expect the backend to redirect to a URL that the app can handle or a known success page
        const result = await WebBrowser.openAuthSessionAsync(
          session.url,
          Linking.createURL('/subscription') // Expected return URL
        );

        if (result.type === 'success' || result.type === 'dismiss') {
          // Check subscription status
          setLoading(true); // Keep loading state while we verify
          setTimeout(() => {
            fetchUser();
          }, 1000);
        }
      } else {
        Toast.show({
          type: 'error',
          text1: 'Error',
          text2: 'No checkout URL received',
        });
      }
    } catch (error: any) {
      const errorMessage = error?.message || 'Failed to create checkout session';
      Toast.show({
        type: 'error',
        text1: 'Subscription Error',
        text2: errorMessage,
      });
    } finally {
      setLoading(false);
    }
  };

  const handleManageSubscription = async () => {
    setLoading(true);
    try {
      const portal = await createPortalSession();
      await WebBrowser.openBrowserAsync(portal.url, {
        showInRecents: true,
      });
      // Reload user data after portal session
      setTimeout(() => {
        fetchUser();
      }, 2000);
    } catch (error: any) {
      const errorMessage = error?.message || 'Failed to open customer portal';
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: errorMessage,
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCancelSubscription = () => {
    Alert.alert(
      'Cancel Subscription',
      'Are you sure you want to cancel your subscription? You will continue to have access until the end of your billing period.',
      [
        {
          text: 'No',
          style: 'cancel',
        },
        {
          text: 'Yes, Cancel',
          style: 'destructive',
          onPress: async () => {
            setLoading(true);
            try {
              await cancelSubscription();
              await fetchUser();
              Toast.show({
                type: 'success',
                text1: 'Subscription Cancelled',
                text2: 'Your subscription will remain active until the end of the billing period',
              });
            } catch (error: any) {
              const errorMessage = error?.message || 'Failed to cancel subscription';
              Toast.show({
                type: 'error',
                text1: 'Error',
                text2: errorMessage,
              });
            } finally {
              setLoading(false);
            }
          },
        },
      ]
    );
  };

  const tier = user?.tier;
  const isPremium = tier === 'PREMIUM_MONTHLY' || tier === 'PREMIUM_YEARLY';
  const subscriptionInterval = tier === 'PREMIUM_MONTHLY' ? 'month' : (tier === 'PREMIUM_YEARLY' ? 'year' : null);

  if (loadingUser) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={colors.primary.main} />
        <Text style={styles.loadingText}>Loading subscription information...</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
      <Text style={styles.title}>Subscription Plans</Text>
      <Text style={styles.subtitle}>Unlock the full potential of Maigie</Text>

      {isPremium && (
        <View style={styles.currentPlanBadge}>
          <Text style={styles.currentPlanText}>You are currently on the Premium Plan</Text>
        </View>
      )}

      {/* Monthly Plan */}
      <View style={styles.planCard}>
        <View style={styles.planHeader}>
          <View>
            <Text style={styles.planName}>Monthly</Text>
            <Text style={styles.planPrice}>₦3,000<Text style={styles.planPeriod}>/mo</Text></Text>
          </View>
          {isPremium && subscriptionInterval === 'month' && (
            <View style={styles.currentBadge}>
              <Text style={styles.currentBadgeText}>Current</Text>
            </View>
          )}
        </View>
        
        <View style={styles.featureList}>
          {/* ... features ... */}
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>Unlimited AI conversations</Text>
          </View>
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>Advanced study tools</Text>
          </View>
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>Priority support</Text>
          </View>
        </View>

        <TouchableOpacity
          style={[
            styles.subscribeButton,
            styles.outlineButton,
            isPremium && subscriptionInterval === 'month' && styles.currentButton,
            loading && styles.disabledButton,
          ]}
          onPress={() => handleSubscribe('monthly')}
          disabled={loading || (isPremium && subscriptionInterval === 'month')}
        >
          {loading ? (
            <ActivityIndicator size="small" color={colors.primary.main} />
          ) : (
            <Text style={styles.outlineButtonText}>
              {isPremium && subscriptionInterval === 'month' ? 'Current Plan' : (isPremium ? 'Switch to Monthly' : 'Subscribe Monthly')}
            </Text>
          )}
        </TouchableOpacity>
      </View>

      {/* Yearly Plan */}
      <View style={[styles.planCard, styles.recommendedCard]}>
        <View style={styles.bestValueBadge}>
          <Text style={styles.bestValueText}>BEST VALUE</Text>
        </View>
        
        <View style={styles.planHeader}>
          <View>
            <Text style={styles.planName}>Yearly</Text>
            <Text style={styles.planPrice}>₦30,000<Text style={styles.planPeriod}>/yr</Text></Text>
            <Text style={styles.savingsText}>Save 17% (₦6,000/year)</Text>
          </View>
          {isPremium && subscriptionInterval === 'year' && (
            <View style={styles.currentBadge}>
              <Text style={styles.currentBadgeText}>Current</Text>
            </View>
          )}
        </View>

        <View style={styles.featureList}>
          {/* ... features ... */}
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>All Monthly features</Text>
          </View>
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>2 months free</Text>
          </View>
          <View style={styles.featureItem}>
            <Check size={16} color={colors.primary.main} />
            <Text style={styles.featureText}>Early access to new features</Text>
          </View>
        </View>

        <TouchableOpacity
          style={[
            styles.subscribeButton,
            isPremium && subscriptionInterval === 'year' && styles.currentButton,
            loading && styles.disabledButton,
          ]}
          onPress={() => handleSubscribe('yearly')}
          disabled={loading || (isPremium && subscriptionInterval === 'year')}
        >
          {loading ? (
            <ActivityIndicator size="small" color={colors.text.white} />
          ) : (
            <Text style={styles.subscribeButtonText}>
              {isPremium && subscriptionInterval === 'year' ? 'Current Plan' : (isPremium ? 'Switch to Yearly' : 'Subscribe Yearly')}
            </Text>
          )}
        </TouchableOpacity>
      </View>

      {isPremium && (
        <View style={styles.managementSection}>
          <TouchableOpacity
            style={[styles.textButton, loading && styles.disabledButton]}
            onPress={handleManageSubscription}
            disabled={loading}
          >
            <Text style={styles.textButtonText}>Manage Subscription</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.textButton, loading && styles.disabledButton]}
            onPress={handleCancelSubscription}
            disabled={loading}
          >
            <Text style={[styles.textButtonText, { color: colors.status.danger }]}>Cancel Subscription</Text>
          </TouchableOpacity>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background.secondary,
  },
  contentContainer: {
    padding: 20,
    paddingTop: 60, // Added top padding as requested
    paddingBottom: 40,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.background.primary,
  },
  loadingText: {
    marginTop: 10,
    color: colors.text.tertiary,
    fontSize: 14,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: colors.text.primary,
    marginBottom: 8,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: 16,
    color: colors.text.tertiary,
    marginBottom: 24,
    textAlign: 'center',
  },
  currentPlanBadge: {
    backgroundColor: colors.background.primary,
    padding: 12,
    borderRadius: 10,
    marginBottom: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.border.default,
  },
  currentPlanText: {
    color: colors.status.success,
    fontSize: 14,
    fontWeight: '600',
  },
  planCard: {
    backgroundColor: colors.background.primary,
    borderRadius: 16,
    padding: 24,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: colors.border.default,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  recommendedCard: {
    borderColor: colors.primary.main,
    borderWidth: 2,
    transform: [{ scale: 1.02 }],
  },
  planHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 20,
  },
  planName: {
    fontSize: 18,
    fontWeight: '700',
    color: colors.text.primary,
    marginBottom: 4,
  },
  planPrice: {
    fontSize: 32,
    fontWeight: '900',
    color: colors.primary.main,
  },
  planPeriod: {
    fontSize: 16,
    fontWeight: '500',
    color: colors.text.tertiary,
  },
  savingsText: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.status.success,
    marginTop: 4,
  },
  bestValueBadge: {
    position: 'absolute',
    top: -12,
    right: 20,
    backgroundColor: colors.primary.main,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  bestValueText: {
    color: colors.text.white,
    fontSize: 12,
    fontWeight: '800',
  },
  currentBadge: {
    backgroundColor: colors.status.success,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 999,
  },
  currentBadgeText: {
    color: colors.text.white,
    fontSize: 10,
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  featureList: {
    marginBottom: 24,
    gap: 12,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  featureText: {
    fontSize: 15,
    color: colors.text.secondary,
    flex: 1,
  },
  subscribeButton: {
    backgroundColor: colors.primary.main,
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: 'center',
  },
  outlineButton: {
    backgroundColor: 'transparent',
    borderWidth: 2,
    borderColor: colors.primary.main,
  },
  currentButton: {
    backgroundColor: colors.border.default,
    borderColor: colors.border.default,
  },
  disabledButton: {
    opacity: 0.6,
  },
  subscribeButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '700',
  },
  outlineButtonText: {
    color: colors.primary.main,
    fontSize: 16,
    fontWeight: '700',
  },
  managementSection: {
    marginTop: 16,
    alignItems: 'center',
    gap: 12,
  },
  textButton: {
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  textButtonText: {
    color: colors.text.tertiary,
    fontSize: 15,
    fontWeight: '600',
  },
  cancelButton: {
    backgroundColor: colors.status.danger,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  cancelButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '700',
  },
  manageButton: {
    backgroundColor: colors.primary.main,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
    marginBottom: 12,
  },
  manageButtonText: {
    color: colors.text.white,
    fontSize: 16,
    fontWeight: '700',
  },
});
