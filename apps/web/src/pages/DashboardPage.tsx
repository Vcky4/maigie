/**
 * Dashboard page component
 */

import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../features/auth/store/authStore';
import { AuthButton } from '../features/auth/components/AuthButton';

export function DashboardPage() {
  const navigate = useNavigate();
  const { logout, user } = useAuthStore();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="max-w-md w-full bg-white rounded-lg shadow-md p-8">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-semibold text-charcoal mb-2">
            Dashboard
          </h1>
          {user && (
            <p className="text-gray-600">
              Welcome, {user.name || user.email}!
            </p>
          )}
        </div>

        <div className="space-y-4">
          <AuthButton
            type="button"
            onClick={handleLogout}
            variant="primary"
            className="w-full"
          >
            Logout
          </AuthButton>
        </div>
      </div>
    </div>
  );
}

