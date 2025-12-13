import React, { useState } from 'react';
import { useAuthStore } from '../features/auth/store/authStore';
import { subscriptionApi } from '../features/subscription/services/subscriptionApi';
import { Check, CreditCard, AlertTriangle, CheckCircle } from 'lucide-react';
import { cn } from '../lib/utils';

export function SubscriptionPage() {
  const user = useAuthStore((state) => state.user);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const handleSubscribe = async (period: 'monthly' | 'yearly') => {
    setIsLoading(true);
    setError(null);
    setSuccessMessage(null);
    try {
      const response = await subscriptionApi.createCheckoutSession(period);
      
      // If subscription was modified directly (upgrade/downgrade)
      if (response.modified && !response.url) {
        const isUpgrade = response.is_upgrade;
        const periodEnd = response.current_period_end 
          ? new Date(response.current_period_end).toLocaleDateString()
          : 'the end of your billing period';
        
        if (isUpgrade) {
          setSuccessMessage(
            `Your subscription has been upgraded! You've been charged a prorated amount, and your new billing cycle starts on ${periodEnd}.`
          );
        } else {
          setSuccessMessage(
            `Your subscription will be downgraded on ${periodEnd}. You'll be charged the new rate at that time.`
          );
        }
        
        // Refresh user data to show updated tier
        setTimeout(() => {
          window.location.reload();
        }, 2000);
      } else if (response.url) {
        // New subscription - redirect to Stripe checkout
        window.location.href = response.url;
      }
    } catch (err: any) {
      const errorMessage = err?.response?.data?.detail || 'Failed to process subscription. Please try again.';
      setError(errorMessage);
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleManageSubscription = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await subscriptionApi.createPortalSession();
      window.location.href = response.url;
    } catch (err) {
      setError('Failed to open subscription portal. Please try again.');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const isPremium = user?.tier !== 'FREE';
  const tierName = user?.tier === 'PREMIUM_YEARLY' ? 'Premium Yearly' : 
                   user?.tier === 'PREMIUM_MONTHLY' ? 'Premium Monthly' : 'Free Plan';

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Subscription</h1>
        <p className="text-gray-500 mt-1">Manage your plan and billing details.</p>
      </div>

      {error && (
        <div className="bg-red-50 text-red-600 p-4 rounded-lg flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          {error}
        </div>
      )}

      {successMessage && (
        <div className="bg-green-50 text-green-700 p-4 rounded-lg flex items-center gap-2">
          <CheckCircle className="w-5 h-5" />
          {successMessage}
        </div>
      )}

      {/* Current Plan Status */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Current Plan</h2>
            <div className="flex items-center gap-2 mt-1">
              <span className={cn(
                "px-2.5 py-0.5 rounded-full text-sm font-medium",
                isPremium ? "bg-primary/10 text-primary" : "bg-gray-100 text-gray-600"
              )}>
                {tierName}
              </span>
              {isPremium && (
                <span className="text-sm text-gray-500">
                  Active
                </span>
              )}
            </div>
          </div>
          
          {isPremium ? (
            <button
              onClick={handleManageSubscription}
              disabled={isLoading}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <CreditCard className="w-4 h-4" />
              Manage Subscription
            </button>
          ) : null}
        </div>
      </div>

      {/* Plans Selection */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Monthly Plan */}
        <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm hover:shadow-md transition-shadow">
          <h3 className="text-lg font-bold text-gray-900">Premium Monthly</h3>
          <div className="mt-2 flex items-baseline">
            <span className="text-3xl font-extrabold text-gray-900">$*.99</span>
            <span className="text-gray-500 ml-1">/mo</span>
          </div>
          <p className="mt-1 text-xs text-primary font-medium">Billed monthly</p>
          <p className="mt-4 text-sm text-gray-500">Flexible monthly billing for consistent learners.</p>
          
          <ul className="mt-6 space-y-3">
            {['Unlimited AI chat', 'Unlimited courses', 'Priority support', 'Premium resources'].map((feature) => (
              <li key={feature} className="flex items-start text-sm text-gray-600">
                <Check className="w-4 h-4 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                {feature}
              </li>
            ))}
          </ul>

          <button
            onClick={() => handleSubscribe('monthly')}
            disabled={isLoading || user?.tier === 'PREMIUM_MONTHLY'}
            className={cn(
              "mt-8 w-full font-semibold py-2.5 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
              user?.tier === 'PREMIUM_MONTHLY'
                ? "bg-gray-100 text-gray-500 border border-gray-200"
                : user?.tier === 'PREMIUM_YEARLY'
                ? "bg-white border-2 border-orange-500 text-orange-600 hover:bg-orange-50"
                : "bg-white border-2 border-primary text-primary hover:bg-primary/5"
            )}
          >
            {user?.tier === 'PREMIUM_MONTHLY' 
              ? 'Current Plan' 
              : user?.tier === 'PREMIUM_YEARLY'
              ? 'Downgrade to Monthly'
              : 'Subscribe Monthly'}
          </button>
        </div>

        {/* Yearly Plan */}
        <div className="bg-white rounded-xl border border-primary ring-1 ring-primary p-6 shadow-md relative">
          <div className="absolute top-0 right-0 bg-primary text-white text-xs font-bold px-3 py-1 rounded-bl-lg rounded-tr-lg">
            BEST VALUE
          </div>
          <h3 className="text-lg font-bold text-gray-900">Premium Yearly</h3>
          <div className="mt-2 flex items-baseline">
            <span className="text-3xl font-extrabold text-gray-900">$*.99</span>
            <span className="text-gray-500 ml-1">/mo</span>
          </div>
          <p className="mt-1 text-xs text-primary font-medium">Billed yearly (Save 20%)</p>
          <p className="mt-4 text-sm text-gray-500">Commit to your learning journey and save.</p>
          
          <ul className="mt-6 space-y-3">
            {['All Monthly features', 'Offline mode', 'Detailed analytics', 'Early access'].map((feature) => (
              <li key={feature} className="flex items-start text-sm text-gray-600">
                <Check className="w-4 h-4 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                {feature}
              </li>
            ))}
          </ul>

          <button
            onClick={() => handleSubscribe('yearly')}
            disabled={isLoading || user?.tier === 'PREMIUM_YEARLY'}
            className={cn(
              "mt-8 w-full font-semibold py-2.5 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
              user?.tier === 'PREMIUM_YEARLY'
                ? "bg-gray-100 text-gray-500 border border-gray-200"
                : user?.tier === 'PREMIUM_MONTHLY'
                ? "bg-primary text-white hover:bg-primary/90"
                : "bg-primary text-white hover:bg-primary/90"
            )}
          >
            {user?.tier === 'PREMIUM_YEARLY' 
              ? 'Current Plan' 
              : user?.tier === 'PREMIUM_MONTHLY'
              ? 'Upgrade to Yearly'
              : 'Subscribe Yearly'}
          </button>
        </div>
      </div>
    </div>
  );
}

