import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { User, CreditCard } from 'lucide-react';
import { cn } from '../lib/utils';
import { ProfileSettings } from './settings/ProfileSettings';
import { SubscriptionPage } from './SubscriptionPage';
import { useCurrentUser } from '../features/auth/hooks/useCurrentUser';

export function SettingsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  
  // Fetch fresh user data when settings page is loaded
  // This will update the auth store which child components use
  useCurrentUser();
  
  // Determine active tab from URL query param or default to 'profile'
  const searchParams = new URLSearchParams(location.search);
  const activeTab = searchParams.get('tab') || 'profile';

  const tabs = [
    { id: 'profile', name: 'Profile', icon: User, component: ProfileSettings },
    { id: 'subscription', name: 'Subscription', icon: CreditCard, component: SubscriptionPage },
  ];

  const handleTabChange = (tabId: string) => {
    navigate(`/settings?tab=${tabId}`);
  };

  const ActiveComponent = tabs.find(t => t.id === activeTab)?.component || ProfileSettings;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Manage your account preferences and subscription.</p>
      </div>

      <div className="flex flex-col md:flex-row gap-6">
        {/* Settings Navigation */}
        <nav className="w-full md:w-64 flex-shrink-0 space-y-1">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            
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

        {/* Content Area */}
        <div className="flex-1 min-w-0">
          <ActiveComponent />
        </div>
      </div>
    </div>
  );
}

