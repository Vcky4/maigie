/**
 * Maigie logo component for auth pages
 */

import { Link } from 'react-router-dom';

export function AuthLogo() {
  return (
    <Link to="/" className="flex items-center justify-start md:justify-center">
      <img src="/assets/logo.png" alt="Maigie Logo" className="h-8 w-auto" />
    </Link>
  );
}

