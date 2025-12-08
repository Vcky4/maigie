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

// Backend API base URL - defaults to localhost:8000 for development
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const WAITLIST_ENDPOINT = `${API_BASE_URL}/api/v1/waitlist/signup`;

export interface WaitlistResponse {
  success: boolean;
  contact_id?: number;
  message: string;
}

export interface WaitlistError {
  detail?: string;
  message?: string;
}

/**
 * Create a contact in Brevo (formerly Sendinblue) CRM via backend API.
 * 
 * The backend handles the actual Brevo API integration, keeping the API key secure.
 * 
 * @param email - The email address of the contact
 * @returns Promise resolving to the waitlist signup response
 */
export async function createContactInBrevo(
  email: string
): Promise<{ success: boolean; contactId?: number; error?: string }> {
  // Validate email format
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { success: false, error: 'Invalid email address' };
  }

  try {
    const response = await fetch(WAITLIST_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email }),
    });

    // Handle successful creation
    if (response.status === 201) {
      const data = (await response.json()) as WaitlistResponse;
      console.log('Contact created successfully via backend:', email);
      return { 
        success: true, 
        contactId: data.contact_id 
      };
    }

    // Handle errors
    if (response.status === 400) {
      const error = (await response.json()) as WaitlistError;
      console.error('Backend API error (400):', error);
      return { 
        success: false, 
        error: error.detail || error.message || 'Invalid request' 
      };
    }

    if (response.status === 503) {
      const error = (await response.json()) as WaitlistError;
      console.error('Backend API error (503):', error);
      return { 
        success: false, 
        error: error.detail || 'Service unavailable' 
      };
    }

    // Handle other errors
    const errorText = await response.text();
    let errorMessage = `API error: ${response.status}`;
    try {
      const error = JSON.parse(errorText) as WaitlistError;
      errorMessage = error.detail || error.message || errorMessage;
    } catch {
      // If parsing fails, use the text as-is
      errorMessage = errorText || errorMessage;
    }
    
    console.error('Backend API error:', response.status, errorMessage);
    return { success: false, error: errorMessage };
  } catch (error) {
    // Network errors or other exceptions
    console.error('Failed to create contact via backend:', error);
    const errorMessage = error instanceof Error ? error.message : 'Network error';
    return { success: false, error: errorMessage };
  }
}

