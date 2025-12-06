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
    <div className="min-h-screen bg-white md:bg-[#F3F5F7] md:flex md:items-center md:justify-center md:px-4 md:py-12 md:sm:px-6 md:lg:px-8">
      <div
        className={cn(
          'w-full min-h-screen bg-white md:h-auto md:min-h-0 md:max-w-md md:rounded-2xl md:shadow-xl p-4 md:p-8 md:sm:p-10 flex flex-col',
          className
        )}
      >
        {children}
      </div>
    </div>
  );
}

