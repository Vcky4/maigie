/**
 * 6-digit OTP input with auto-advance functionality
 */

import { useRef, useState, KeyboardEvent, ChangeEvent, ClipboardEvent } from 'react';
import { cn } from '../../../lib/utils';

interface OTPCodeInputProps {
  value: string;
  onChange: (value: string) => void;
  error?: string;
}

export function OTPCodeInput({ value, onChange, error }: OTPCodeInputProps) {
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);
  const [focusedIndex, setFocusedIndex] = useState(0);

  const handleChange = (index: number, e: ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value.replace(/[^0-9]/g, '');
    
    if (newValue.length > 1) {
      // Handle paste
      const pastedValue = newValue.slice(0, 6);
      onChange(pastedValue);
      
      // Focus the last filled input or the next empty one
      const nextIndex = Math.min(pastedValue.length, 5);
      inputRefs.current[nextIndex]?.focus();
      setFocusedIndex(nextIndex);
      return;
    }

    const newCode = value.split('');
    newCode[index] = newValue;
    const updatedCode = newCode.join('').slice(0, 6);
    onChange(updatedCode);

    // Auto-advance to next input
    if (newValue && index < 5) {
      inputRefs.current[index + 1]?.focus();
      setFocusedIndex(index + 1);
    }
  };

  const handleKeyDown = (index: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !value[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
      setFocusedIndex(index - 1);
    } else if (e.key === 'ArrowLeft' && index > 0) {
      inputRefs.current[index - 1]?.focus();
      setFocusedIndex(index - 1);
    } else if (e.key === 'ArrowRight' && index < 5) {
      inputRefs.current[index + 1]?.focus();
      setFocusedIndex(index + 1);
    }
  };

  const handleFocus = (index: number) => {
    setFocusedIndex(index);
  };

  const handlePaste = (e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    const pastedData = e.clipboardData.getData('text');
    
    // Extract only digits from pasted data
    const digitsOnly = pastedData.replace(/[^0-9]/g, '');
    
    if (digitsOnly.length === 0) {
      return;
    }

    // Take only the first 6 digits
    const otpValue = digitsOnly.slice(0, 6);
    onChange(otpValue);

    // Focus the next empty input or the last input if all are filled
    const nextIndex = Math.min(otpValue.length, 5);
    setTimeout(() => {
      inputRefs.current[nextIndex]?.focus();
      setFocusedIndex(nextIndex);
    }, 0);
  };

  return (
    <div className="w-full">
      <div className="flex gap-2 justify-center">
        {Array.from({ length: 6 }).map((_, index) => (
          <input
            key={index}
            ref={(el) => { inputRefs.current[index] = el; }}
            type="text"
            inputMode="numeric"
            maxLength={1}
            value={value[index] || ''}
            onChange={(e) => handleChange(index, e)}
            onKeyDown={(e) => handleKeyDown(index, e)}
            onFocus={() => handleFocus(index)}
            onPaste={handlePaste}
            className={cn(
              'w-12 h-12 sm:w-14 sm:h-14 text-center text-xl font-semibold',
              'rounded-lg border-2 transition-all duration-200',
              'focus:outline-none focus:ring-2 focus:ring-primary',
              error
                ? 'border-red-300 focus:ring-red-500'
                : focusedIndex === index
                ? 'border-primary ring-2 ring-primary'
                : 'border-gray-300',
              'focus:border-primary'
            )}
            aria-label={`OTP digit ${index + 1}`}
          />
        ))}
      </div>
      {error && (
        <p className="mt-2 text-sm text-red-600 text-center" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

