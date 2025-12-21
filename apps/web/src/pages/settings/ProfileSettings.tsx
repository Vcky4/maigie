import React, { useState } from 'react';
import { useAuthStore } from '../../features/auth/store/authStore';
import { useCurrentUser } from '../../features/auth/hooks/useCurrentUser';
import { User, Mail, Shield, AlertCircle, Check } from 'lucide-react';
import { authApi } from '../../features/auth/services/authApi';

export function ProfileSettings() {
  const { user: apiUser, isLoading } = useCurrentUser();
  const storeUser = useAuthStore((state) => state.user);
  
  // Use apiUser if available (freshest), otherwise fall back to storeUser
  const user = apiUser || storeUser;

  // Password change state
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [passwordForm, setPasswordForm] = useState({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  });
  const [passwordError, setPasswordError] = useState('');
  const [passwordSuccess, setPasswordSuccess] = useState('');
  const [isSubmittingPassword, setIsSubmittingPassword] = useState(false);

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    setPasswordSuccess('');

    if (passwordForm.newPassword !== passwordForm.confirmPassword) {
      setPasswordError("New passwords don't match");
      return;
    }

    if (passwordForm.newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters");
      return;
    }

    setIsSubmittingPassword(true);

    try {
      await authApi.changePassword({
        currentPassword: passwordForm.currentPassword,
        newPassword: passwordForm.newPassword,
      });
      setPasswordSuccess('Password changed successfully');
      setPasswordForm({ currentPassword: '', newPassword: '', confirmPassword: '' });
      setTimeout(() => {
        setIsChangingPassword(false);
        setPasswordSuccess('');
      }, 2000);
    } catch (err: any) {
      console.error('Change password error:', err);
      setPasswordError(err.response?.data?.detail || 'Failed to change password. Please check your current password.');
    } finally {
      setIsSubmittingPassword(false);
    }
  };

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
        <div className="px-4 py-5 sm:p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 p-2 rounded-lg">
                <Shield className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-gray-900">Password</h3>
                <p className="text-sm text-gray-500">
                  {isChangingPassword ? 'Enter your current password and a new strong password.' : 'Secure your account with a strong password.'}
                </p>
              </div>
            </div>
            {!isChangingPassword && (
              <button
                onClick={() => setIsChangingPassword(true)}
                className="text-sm font-medium text-primary hover:text-primary/90"
              >
                Update
              </button>
            )}
          </div>

          {isChangingPassword && (
            <form onSubmit={handlePasswordSubmit} className="mt-6 space-y-4 max-w-md ml-11">
              <div>
                <label className="block text-sm font-medium text-gray-700">Current Password</label>
                <input
                  type="password"
                  required
                  value={passwordForm.currentPassword}
                  onChange={(e) => setPasswordForm({...passwordForm, currentPassword: e.target.value})}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-primary focus:ring-primary sm:text-sm"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700">New Password</label>
                <input
                  type="password"
                  required
                  value={passwordForm.newPassword}
                  onChange={(e) => setPasswordForm({...passwordForm, newPassword: e.target.value})}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-primary focus:ring-primary sm:text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700">Confirm New Password</label>
                <input
                  type="password"
                  required
                  value={passwordForm.confirmPassword}
                  onChange={(e) => setPasswordForm({...passwordForm, confirmPassword: e.target.value})}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-primary focus:ring-primary sm:text-sm"
                />
              </div>

              {passwordError && (
                <div className="flex items-center text-sm text-red-600">
                  <AlertCircle className="w-4 h-4 mr-2" />
                  {passwordError}
                </div>
              )}

              {passwordSuccess && (
                <div className="flex items-center text-sm text-green-600">
                  <Check className="w-4 h-4 mr-2" />
                  {passwordSuccess}
                </div>
              )}

              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={isSubmittingPassword}
                  className="inline-flex justify-center rounded-md border border-transparent bg-primary py-2 px-4 text-sm font-medium text-white shadow-sm hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:opacity-50"
                >
                  {isSubmittingPassword ? 'Saving...' : 'Save Password'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsChangingPassword(false);
                    setPasswordError('');
                    setPasswordForm({ currentPassword: '', newPassword: '', confirmPassword: '' });
                  }}
                  className="inline-flex justify-center rounded-md border border-gray-300 bg-white py-2 px-4 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
