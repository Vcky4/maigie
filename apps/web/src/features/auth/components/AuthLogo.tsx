/**
 * Maigie logo component for auth pages
 */

import { Link } from 'react-router-dom';

export function AuthLogo() {
  return (
    <Link to="/" className="flex items-center justify-center">
      <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center mr-2">
        <span className="text-white font-bold text-xl">M</span>
      </div>
      <span className="text-2xl font-bold text-gray-900">Maigie</span>
    </Link>
  );
}

