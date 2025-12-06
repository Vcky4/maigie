# Brevo Integration Setup

This document explains how to configure the Brevo (formerly Sendinblue) CRM integration for the waitlist feature.

## Environment Variables

Create a `.env` file in the `apps/web` directory with the following variables:

```bash
# Brevo (formerly Sendinblue) CRM Integration
# Get your API key from https://app.brevo.com/settings/keys/api
VITE_BREVO_API_KEY=xkeysib-your-api-key-here
VITE_BREVO_ENABLED=true
```

## Configuration

1. **Get your Brevo API Key**:
   - Log in to [Brevo Dashboard](https://app.brevo.com)
   - Go to Settings → API Keys
   - Create a new API key or use an existing one
   - Copy the API key (starts with `xkeysib-`)

2. **Set Environment Variables**:
   - Create `.env` file in `apps/web/` directory
   - Add `VITE_BREVO_API_KEY` with your API key
   - Set `VITE_BREVO_ENABLED=true` to enable the integration

3. **Restart Development Server**:
   - After adding environment variables, restart your Vite dev server
   - Environment variables are loaded at build time

## How It Works

When a user submits their email on the waitlist page:
1. The email is sent to Brevo API to create a contact
2. If successful, the contact is added to your Brevo account
3. If the API call fails, the user still sees a success message (graceful degradation)
4. Errors are logged to the console for monitoring

## Security Notes

⚠️ **Important**: The API key will be exposed in the frontend bundle. Ensure:
- The API key has minimal permissions (only create contacts)
- Rate limiting is configured on Brevo's side
- Monitor API usage for unusual patterns

## Testing

To test the integration:
1. Ensure environment variables are set
2. Start the development server: `nx serve web`
3. Navigate to `/waitlist`
4. Submit an email address
5. Check your Brevo dashboard to verify the contact was created

## Troubleshooting

- **Contact not created**: Check browser console for errors
- **API key errors**: Verify the API key is correct and has proper permissions
- **CORS errors**: Brevo API should handle CORS, but check if your domain is allowed
- **Integration disabled**: Set `VITE_BREVO_ENABLED=true` in your `.env` file

