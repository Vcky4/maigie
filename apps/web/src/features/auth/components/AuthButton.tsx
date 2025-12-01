/**
 * Button component with loading states
 */

import { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '../../../lib/utils';

interface AuthButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  variant?: 'primary' | 'secondary';
  children: ReactNode;
}

export function AuthButton({
  loading,
  variant = 'primary',
  children,
  className,
  disabled,
  ...props
}: AuthButtonProps) {
  const isDisabled = disabled || loading;

  return (
    <button
      className={cn(
        'w-full h-12 font-medium transition-all duration-200',
        'focus:outline-none focus:ring-2 focus:ring-offset-2',
        variant === 'primary'
          ? 'bg-indigo-600 text-white hover:bg-indigo-700 focus:ring-indigo-500 shadow-md hover:shadow-lg'
          : 'bg-white text-primary border-2 border-gray-300 hover:border-primary focus:ring-primary',
        isDisabled && 'opacity-50 cursor-not-allowed',
        className
      )}
      style={{
        borderRadius: '50px',
      }}
      disabled={isDisabled}
      {...props}
    >
      {loading ? (
        <span className="flex items-center justify-center">
          <svg
            className="animate-spin -ml-1 mr-3 h-5 w-5"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          Loading...
        </span>
      ) : (
        children
      )}
    </button>
  );
}

