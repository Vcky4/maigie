import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { User, CreditCard, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '../lib/utils';
import { ProfileSettings } from './settings/ProfileSettings';
import { SubscriptionPage } from './SubscriptionPage';
import { useCurrentUser } from '../features/auth/hooks/useCurrentUser';
import { useAuthStore } from '../features/auth/store/authStore';

export function SettingsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user: storeUser } = useAuthStore();
  
  // Fetch fresh user data when settings page is loaded
  const { user: apiUser } = useCurrentUser();
  const user = apiUser || storeUser;
  
  // Determine active tab
  // On mobile, if no tab is selected, we show the main settings menu
  // On desktop, we default to 'profile' if no tab is selected
  const searchParams = new URLSearchParams(location.search);
  const activeTabId = searchParams.get('tab');

  const tabs = [
    { id: 'profile', name: 'Profile', icon: User, component: ProfileSettings },
    { id: 'subscription', name: 'Subscription', icon: CreditCard, component: SubscriptionPage },
  ];

  const handleTabChange = (tabId: string) => {
    navigate(`/settings?tab=${tabId}`);
  };

  // Determine which component to show based on view mode
  const DesktopActiveComponent = tabs.find(t => t.id === (activeTabId || 'profile'))?.component || ProfileSettings;
  const MobileActiveComponent = tabs.find(t => t.id === activeTabId)?.component;

  return (
    <div className="max-w-5xl mx-auto md:space-y-6 min-h-[calc(100vh-4rem)]">
      
      {/* Mobile View */}
      <div className="md:hidden">
        {!activeTabId ? (
          // Landing View (Menu)
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
            </div>

            {/* Profile Summary Card */}
            <div className="bg-white p-4 rounded-lg border border-gray-200 flex items-center gap-4">
               <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-primary font-bold text-lg shrink-0">
                  {user?.name?.[0]?.toUpperCase() || user?.email?.[0]?.toUpperCase() || 'U'}
               </div>
               <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-900 truncate">{user?.name || 'User'}</div>
                  <div className="text-sm text-gray-500 truncate">{user?.email}</div>
               </div>
            </div>

            {/* Settings List */}
            <nav className="space-y-3">
               {tabs.map((tab) => {
                 const Icon = tab.icon;
                 return (
                   <button
                     key={tab.id}
                     onClick={() => handleTabChange(tab.id)}
                     className="w-full flex items-center justify-between p-4 bg-white rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
                   >
                     <div className="flex items-center gap-3">
                       <Icon className="w-5 h-5 text-gray-500" />
                       <span className="font-medium text-gray-900">{tab.name}</span>
                     </div>
                     <ChevronRight className="w-5 h-5 text-gray-400" />
                   </button>
                 );
               })}
            </nav>
          </div>
        ) : (
          // Detail View
          <div className="space-y-6">
            <div className="flex items-center gap-2">
              <button 
                onClick={() => navigate('/settings')}
                className="p-2 -ml-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-full transition-colors"
              >
                <ChevronLeft className="w-6 h-6" />
              </button>
              <h1 className="text-xl font-bold text-gray-900">
                {tabs.find(t => t.id === activeTabId)?.name}
              </h1>
            </div>
            {MobileActiveComponent && <MobileActiveComponent />}
          </div>
        )}
      </div>

      {/* Desktop View */}
      <div className="hidden md:block space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
          <p className="text-gray-500 mt-1">Manage your account preferences and subscription.</p>
        </div>

        <div className="flex flex-row gap-6">
          <nav className="w-64 flex-shrink-0 space-y-1">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = (activeTabId || 'profile') === tab.id;
              
              return (
                <button
                  key={tab.id}
                  onClick={() => handleTabChange(tab.id)}
                  className={cn(
                    "w-full flex items-center gap-3 px-4 py-3 text-sm font-medium rounded-lg transition-colors text-left",
                    isActive 
                      ? "bg-primary/10 text-primary" 
                      : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                  )}
                >
                  <Icon className="w-5 h-5" />
                  {tab.name}
                </button>
              );
            })}
          </nav>

          <div className="flex-1 min-w-0">
            <DesktopActiveComponent />
          </div>
        </div>
      </div>
    </div>
  );
}
