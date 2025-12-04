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

const BREVO_API_URL = 'https://api.brevo.com/v3/contacts';
const BREVO_API_KEY = import.meta.env.VITE_BREVO_API_KEY;
const BREVO_ENABLED = import.meta.env.VITE_BREVO_ENABLED !== 'false';

export interface CreateContactResponse {
  id?: number;
  email?: string;
  [key: string]: unknown;
}

export interface CreateContactError {
  code?: string;
  message?: string;
}

/**
 * Create a contact in Brevo (formerly Sendinblue) CRM.
 * 
 * @param email - The email address of the contact
 * @returns Promise resolving to the contact creation response or null if disabled/failed
 */
export async function createContactInBrevo(
  email: string
): Promise<{ success: boolean; contactId?: number; error?: string }> {
  // Check if Brevo integration is enabled and API key is configured
  if (!BREVO_ENABLED || !BREVO_API_KEY) {
    console.warn('Brevo integration disabled or API key not configured');
    return { success: false, error: 'Integration not configured' };
  }

  // Validate email format
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { success: false, error: 'Invalid email address' };
  }

  try {
    const response = await fetch(BREVO_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'api-key': BREVO_API_KEY,
      },
      body: JSON.stringify({ email }),
    });

    // Handle successful creation (201) or contact already exists (204)
    if (response.status === 201 || response.status === 204) {
      let contactId: number | undefined;
      if (response.status === 201) {
        const data = (await response.json()) as CreateContactResponse;
        contactId = data.id;
      }
      console.log('Contact created successfully in Brevo:', email);
      return { success: true, contactId };
    }

    // Handle errors
    if (response.status === 400) {
      const error = (await response.json()) as CreateContactError;
      console.error('Brevo API error (400):', error);
      return { success: false, error: error.message || 'Invalid request' };
    }

    if (response.status === 401 || response.status === 403) {
      console.error('Brevo API authentication error:', response.status);
      return { success: false, error: 'Authentication failed' };
    }

    // Handle other errors
    const errorText = await response.text();
    console.error('Brevo API error:', response.status, errorText);
    return { success: false, error: `API error: ${response.status}` };
  } catch (error) {
    // Network errors or other exceptions
    console.error('Failed to create contact in Brevo:', error);
    const errorMessage = error instanceof Error ? error.message : 'Network error';
    return { success: false, error: errorMessage };
  }
}

