export interface UserPreferences {
    theme: 'light' | 'dark';
    language: string;
    notifications: boolean;
}

export interface UserResponse {
    id: string;
    email: string;
    name: string | null;
    tier: 'FREE' | 'PREMIUM_MONTHLY' | 'PREMIUM_YEARLY';
    isActive: boolean;
    preferences: UserPreferences;
}

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

export interface SubscriptionContextType {
    createCheckoutSession: (priceType: 'monthly' | 'yearly') => Promise<CheckoutSessionResponse>;
    createPortalSession: () => Promise<PortalSessionResponse>;
    cancelSubscription: () => Promise<CancelSubscriptionResponse>;
}
