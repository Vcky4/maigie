# Subscription & Billing System

## Issue Type
Feature

## Priority
High

## Labels
- subscription
- billing
- monetization
- payment
- mvp

## Description

Implement the complete subscription and billing system supporting free and premium tiers, payment processing, subscription management, and tier-based feature restrictions.

## User Stories

### As a free tier user:
- I want to understand the limitations of my free account
- I want to see a clear comparison of free vs premium features
- I want to upgrade to premium easily
- I want to know how much time/features I have left

### As a premium user:
- I want to manage my subscription
- I want to change my billing information
- I want to view my billing history
- I want to cancel my subscription
- I want to downgrade or upgrade my plan

### As a product owner:
- I want to track subscription conversions
- I want to handle payment failures gracefully
- I want to manage subscription lifecycle automatically

## Functional Requirements

### Subscription Plans

**Free Tier:**
- 50 AI chat messages/month (resets on 1st of each calendar month)
- No voice AI
- Max 2 AI-generated courses (total, not monthly)
- Max 2 active goals (can archive and create new ones)
- Basic scheduling (manual only)
- Limited resource recommendations (10/week)
- Ads present (optional)
- When limits exceeded: soft block with upgrade prompt, no data loss

**Premium Monthly:**
- $9.99/month
- Unlimited AI chat
- Full voice capabilities
- Unlimited courses and goals
- AI-generated schedules
- Unlimited resources
- Multi-device sync
- Advanced analytics
- No ads
- Cancel anytime

**Premium Yearly:**
- $99/year (save 17%)
- All Premium Monthly features
- Annual billing
- Priority support

### Subscription Flows

#### Upgrade Flow
1. User clicks "Upgrade to Premium"
2. Pricing comparison modal displays
3. User selects Monthly or Yearly
4. Redirect to payment provider (Stripe)
5. User completes payment
6. Payment confirmation
7. App updates user role to Premium
8. Premium features unlock immediately
9. Confirmation email sent

#### Downgrade Flow
1. User clicks "Cancel Subscription"
2. Cancellation confirmation dialog
3. User confirms cancellation
4. System records end-of-cycle date
5. User continues with Premium until cycle ends
6. At cycle end:
   - User moved to Free tier
   - Features restricted to free tier limits
   - Courses beyond limit frozen
   - Goals beyond limit frozen
   - AI chat message counter resets monthly

#### Payment Update Flow
1. User navigates to billing settings
2. User clicks "Update Payment Method"
3. Redirect to payment provider
4. User updates card information
5. Confirmation of update
6. Return to app

### Feature Enforcement

#### Free Tier Restrictions
- Message counter displays remaining messages
- Warning at 40 messages (80%)
- Block at 50 messages with upgrade prompt
- Voice features disabled (UI hidden)
- Course creation blocked after 2 AI courses
- Goal creation blocked after 2 active goals
- AI scheduling unavailable

#### Premium Tier Access
- Remove all restrictions
- Enable all features immediately upon payment
- Maintain access through billing cycle
- Grace period for payment failures (3 days)

### Billing Management

- View current plan
- View billing history
- Download invoices
- Update payment method
- View next billing date
- View usage statistics
- Cancel subscription
- Reactivate subscription

### Payment Processing

- Stripe integration for card payments
- Support major credit cards
- Support debit cards
- Alternative payment methods (PayPal, optional)
- Secure payment handling (PCI compliance)
- Payment retry logic
- Failed payment notifications

### Subscription Lifecycle

- **Trial period** (optional): 7 days free
- **Active subscription**: Full access
- **Past due**: 3-day grace period
- **Canceled**: Access until cycle end
- **Expired**: Return to free tier

## Technical Requirements

### Backend
- FastAPI endpoints for subscription
- Prisma model for Subscription and Payment
- Stripe SDK integration
- Webhook handling for Stripe events
- Background worker for subscription checks
- Feature flag system for tier enforcement
- Invoice generation
- Email notifications

### Frontend (Web - Vite + shadcn-ui)
- Pricing page
- Subscription management dashboard
- Payment form (Stripe Elements)
- Billing history view
- Usage statistics display
- Upgrade prompts
- Cancellation flow UI

### Frontend (Mobile - Expo)
- In-app purchase support (iOS)
- Google Play billing (Android)
- Subscription status display
- Upgrade prompts
- Billing management

### Database Schema
```
Subscription {
  id, userId, plan,
  status, startDate, endDate,
  cancelAtPeriodEnd,
  stripeSubscriptionId,
  stripeCustomerId
}

Payment {
  id, userId, subscriptionId,
  amount, currency, status,
  stripePaymentIntentId,
  createdAt
}

UsageQuota {
  userId, month,
  aiMessagesUsed, aiMessagesLimit,
  coursesCreated, coursesLimit,
  goalsCreated, goalsLimit
}
```

## Acceptance Criteria

- [ ] Free tier users see limitations clearly
- [ ] Pricing comparison is easy to understand
- [ ] User can upgrade from free to premium
- [ ] Payment flow completes successfully
- [ ] Stripe payment processing works
- [ ] User role updates immediately after payment
- [ ] Premium features unlock immediately
- [ ] Subscription status displays correctly
- [ ] User can view billing history
- [ ] User can download invoices
- [ ] User can update payment method
- [ ] User can cancel subscription
- [ ] Canceled users retain access until cycle end
- [ ] At cycle end, users downgrade to free tier
- [ ] Downgraded users have features restricted
- [ ] Message counter works accurately
- [ ] Message limit enforced at 50 messages
- [ ] Course creation blocked at limit
- [ ] Goal creation blocked at limit
- [ ] Voice features disabled for free tier
- [ ] Upgrade prompts appear at appropriate times
- [ ] Failed payment notifications sent
- [ ] Payment retry logic works
- [ ] Grace period honored (3 days)
- [ ] Usage statistics are accurate
- [ ] Webhooks handle all Stripe events
- [ ] Subscription sync is reliable
- [ ] Multiple payment methods supported
- [ ] Mobile in-app purchases work (iOS/Android)

## API Endpoints

- `GET /api/subscription` - Get user subscription status
- `POST /api/subscription/create` - Create new subscription
- `POST /api/subscription/cancel` - Cancel subscription
- `POST /api/subscription/reactivate` - Reactivate subscription
- `GET /api/subscription/plans` - Get available plans
- `POST /api/subscription/checkout` - Create checkout session
- `POST /api/subscription/portal` - Create customer portal session
- `GET /api/billing/history` - Get payment history
- `GET /api/billing/invoice/:id` - Get invoice details
- `POST /api/webhooks/stripe` - Stripe webhook endpoint
- `GET /api/usage` - Get usage statistics
- `GET /api/features/check` - Check feature availability

## Stripe Webhook Events

- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `customer.updated`

## UI Components

- PricingTable
- PlanCard
- UpgradePrompt
- SubscriptionDashboard
- BillingHistory
- InvoiceViewer
- PaymentMethodForm
- UsageChart
- FeatureComparison
- CancellationDialog

## Dependencies

- Stripe account and API keys
- Email service for notifications
- Invoice PDF generation
- Mobile app store accounts (for in-app purchases)

## Performance Requirements

- Checkout page loads in < 1 second
- Payment processing < 3 seconds
- Webhook processing < 500ms
- Feature checks < 100ms
- Support 10,000+ subscriptions

## Security Considerations

- PCI DSS compliance via Stripe
- Secure webhook signature verification
- Encrypted payment data
- No storage of raw card numbers
- HTTPS only for payment pages
- Rate limiting on subscription changes

## Testing Requirements

- Unit tests for tier enforcement
- Integration tests with Stripe test mode
- E2E tests for upgrade/downgrade flows
- Webhook handling tests
- Payment failure tests
- Usage quota tests
- Mobile in-app purchase tests

## Compliance & Legal

- Privacy policy for payment data
- Terms of service for subscriptions
- Refund policy
- Cancellation policy
- Tax handling (if applicable)
- EU VAT compliance (if applicable)

## Estimated Effort
Large - 3-4 sprints

## Related Issues
- All feature modules (tier enforcement)
- Authentication (user tier tracking)
- Dashboard (subscription status display)
- Analytics (conversion tracking)
