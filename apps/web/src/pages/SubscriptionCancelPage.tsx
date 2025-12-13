import React from 'react';
import { useNavigate } from 'react-router-dom';
import { XCircle } from 'lucide-react';

export function SubscriptionCancelPage() {
  const navigate = useNavigate();

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center text-center px-4">
      <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mb-6">
        <XCircle className="w-8 h-8 text-red-600" />
      </div>
      <h1 className="text-3xl font-bold text-gray-900 mb-2">Subscription Cancelled</h1>
      <p className="text-gray-600 max-w-md mb-8">
        Your subscription process was cancelled. No charges were made.
      </p>
      <div className="flex gap-4">
        <button
          onClick={() => navigate('/subscription')}
          className="bg-primary text-white px-6 py-2.5 rounded-lg font-medium hover:bg-primary/90 transition-colors"
        >
          Try Again
        </button>
        <button
          onClick={() => navigate('/dashboard')}
          className="bg-gray-100 text-gray-700 px-6 py-2.5 rounded-lg font-medium hover:bg-gray-200 transition-colors"
        >
          Back to Dashboard
        </button>
      </div>
    </div>
  );
}

