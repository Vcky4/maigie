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

import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * Auth redirect page for mobile OAuth callbacks
 * Detects mobile browsers and redirects to custom scheme deep link
 * Preserves all query parameters from the OAuth callback
 */
export function AuthRedirectPage() {
  const [searchParams] = useSearchParams();

  useEffect(() => {
    // Detect if this is being opened in a mobile in-app browser
    const isMobileBrowser =
      /Mobile|Android|iPhone|iPad/i.test(navigator.userAgent) ||
      (window.navigator as any).standalone === true ||
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);

    if (isMobileBrowser) {
      // Get all query parameters and build the deep link URL
      const queryString = searchParams.toString();
      const deepLink = `${import.meta.env.VITE_MOBILE_URL}${queryString ? `?${queryString}` : ''}`;
      
      // Redirect immediately to close the browser
      window.location.replace(deepLink);
    }
  }, [searchParams]);

  // For non-mobile browsers, show a simple message
  // (This shouldn't normally happen as web OAuth should use a different route)
  return (
    <div style={{ 
      display: 'flex', 
      flexDirection: 'column', 
      alignItems: 'center', 
      justifyContent: 'center', 
      minHeight: '100vh',
      padding: '2rem',
      textAlign: 'center'
    }}>
      <h1>Redirecting...</h1>
      <p>If you're not redirected automatically, please open this link in the Maigie app.</p>
    </div>
  );
}

