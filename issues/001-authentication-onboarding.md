# Authentication & Onboarding

## Issue Type
Feature

## Priority
Critical

## Labels
- authentication
- onboarding
- user-experience
- mvp

## Description

Implement the complete authentication and onboarding flow for Maigie, allowing users to sign up, log in, and set their initial preferences.

## User Stories

### As a new user:
- I want to sign up using email/password or OAuth (Google)
- I want to confirm my email (optional for MVP)
- I want to set my study preferences during onboarding
- I want the system to understand my learning needs from the start

### As a returning user:
- I want to log in securely
- I want my session to be maintained across devices

## Functional Requirements

### Sign Up
- Email/password registration
- OAuth integration (Google)
- Email confirmation (optional for MVP)
- Password strength validation
- Error handling for duplicate accounts

### Onboarding Flow
1. User completes registration
2. User sets preferences:
   - Study interests
   - Academic level
   - Time availability
   - Timezone
3. AI introduces itself with a welcome message
4. User is directed to the AI chat hub

### Login
- Email/password authentication
- OAuth login (Google)
- Remember me functionality
- Password reset flow

## Technical Requirements

### Backend
- FastAPI authentication endpoints
- JWT token generation and validation
- OAuth provider integration
- User model with preference fields
- Secure password hashing (bcrypt)
- Session management

### Frontend (Web - Vite + shadcn-ui)
- Signup form with validation
- Login form
- OAuth buttons
- Onboarding wizard/stepper
- Preference selection UI
- Form error handling

### Frontend (Mobile - Expo)
- Native authentication screens
- OAuth integration for mobile
- Onboarding flow optimized for mobile
- Secure token storage

## Acceptance Criteria

- [ ] User can sign up with email/password
- [ ] User can sign up with Google OAuth
- [ ] Password meets strength requirements
- [ ] Duplicate email addresses are rejected with clear error message
- [ ] User can complete onboarding preferences
- [ ] User preferences are saved to database
- [ ] User is redirected to AI chat hub after onboarding
- [ ] User can log in with email/password
- [ ] User can log in with Google OAuth
- [ ] Invalid credentials show appropriate error messages
- [ ] Password reset email is sent successfully
- [ ] JWT tokens are properly generated and validated
- [ ] Sessions persist across page refreshes
- [ ] Authentication works on both web and mobile platforms

## API Endpoints

- `POST /api/auth/signup` - Create new user account
- `POST /api/auth/login` - Authenticate user
- `POST /api/auth/oauth/google` - OAuth authentication
- `POST /api/auth/logout` - End user session
- `POST /api/auth/reset-password` - Request password reset
- `POST /api/auth/confirm-email` - Confirm email address
- `GET /api/auth/me` - Get current user info
- `PUT /api/users/preferences` - Update user preferences

## Dependencies

- OAuth provider setup (Google)
- Email service provider (for confirmation/reset emails)
- Database schema for User and UserPreferences models

## Security Considerations

- Passwords hashed with bcrypt
- JWT tokens with appropriate expiration
- HTTPS only for authentication endpoints
- Rate limiting on login attempts
- CSRF protection
- XSS prevention

## Testing Requirements

- Unit tests for authentication logic
- Integration tests for auth endpoints
- E2E tests for signup/login flows
- Security testing for common vulnerabilities

## Estimated Effort
Medium - 2-3 sprints

## Related Issues
- Subscription system (for user tier management)
- Dashboard (post-authentication landing)
