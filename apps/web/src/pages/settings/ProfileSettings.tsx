import React from 'react';
import { useAuthStore } from '../../features/auth/store/authStore';
import { useCurrentUser } from '../../features/auth/hooks/useCurrentUser';
import { User, Mail, Shield } from 'lucide-react';

export function ProfileSettings() {
  const { user: apiUser, isLoading } = useCurrentUser();
  const storeUser = useAuthStore((state) => state.user);
  
  // Use apiUser if available (freshest), otherwise fall back to storeUser
  const user = apiUser || storeUser;

  if (isLoading && !user) {
    return (
      <div className="flex justify-center items-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-medium text-gray-900">Profile Information</h2>
        <p className="mt-1 text-sm text-gray-500">
          View your account details.
        </p>
      </div>

      <div className="bg-white shadow rounded-lg border border-gray-200">
        <div className="px-4 py-5 sm:p-6 space-y-6">
          <div className="flex items-center gap-4">
            <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xl font-bold">
              {user?.name?.charAt(0).toUpperCase() || <User className="w-8 h-8" />}
            </div>
            <div>
              <h3 className="text-lg font-medium text-gray-900">{user?.name || 'User'}</h3>
              <p className="text-sm text-gray-500">Member since {new Date((user as unknown as { createdAt?: string })?.createdAt || Date.now()).toLocaleDateString()}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700">Full Name</label>
              <div className="mt-1 relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <User className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  type="text"
                  readOnly
                  value={user?.name || ''}
                  className="focus:ring-primary focus:border-primary block w-full pl-10 sm:text-sm border-gray-300 rounded-md bg-gray-50"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700">Email Address</label>
              <div className="mt-1 relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Mail className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  type="email"
                  readOnly
                  value={user?.email || ''}
                  className="focus:ring-primary focus:border-primary block w-full pl-10 sm:text-sm border-gray-300 rounded-md bg-gray-50"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-medium text-gray-900">Security</h2>
        <p className="mt-1 text-sm text-gray-500">
          Manage your account security settings.
        </p>
      </div>

      <div className="bg-white shadow rounded-lg border border-gray-200 divide-y divide-gray-200">
        <div className="px-4 py-5 sm:p-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-blue-100 p-2 rounded-lg">
              <Shield className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-sm font-medium text-gray-900">Password</h3>
              <p className="text-sm text-gray-500">Change your password via reset flow.</p>
            </div>
          </div>
          <a
            href="/forgot-password"
            className="text-sm font-medium text-primary hover:text-primary/90"
          >
            Update
          </a>
        </div>
      </div>
    </div>
  );
}
