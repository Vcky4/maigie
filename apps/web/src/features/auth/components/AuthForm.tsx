/**
 * Base form wrapper component for auth pages
 */

import { ReactNode } from 'react';
import { cn } from '../../../lib/utils';

interface AuthFormProps {
  children: ReactNode;
  className?: string;
}

export function AuthForm({ children, className }: AuthFormProps) {
  return (
    <div className="min-h-screen md:flex md:items-center md:justify-center md:px-4 md:py-12 md:sm:px-6 md:lg:px-8" style={{ backgroundColor: '#F3F5F7' }}>
      <div
        className={cn(
          'w-full h-full min-h-screen bg-white md:h-auto md:min-h-0 md:max-w-md md:rounded-2xl p-8 sm:p-10',
          className
        )}
        style={{
          boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
        }}
      >
        {children}
      </div>
    </div>
  );
}

