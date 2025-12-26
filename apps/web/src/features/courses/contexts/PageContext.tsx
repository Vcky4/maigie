import React, { createContext, useContext, useState, useCallback, useMemo, ReactNode } from 'react';

interface PageContextType {
  courseId?: string;
  topicId?: string;
  noteId?: string;
  setContext: (context: { courseId?: string; topicId?: string; noteId?: string }) => void;
  clearContext: () => void;
}

const PageContext = createContext<PageContextType | undefined>(undefined);

export const PageContextProvider = ({ children }: { children: ReactNode }) => {
  const [context, setContextState] = useState<{
    courseId?: string;
    topicId?: string;
    noteId?: string;
  }>({});

  const setContext = useCallback((newContext: { courseId?: string; topicId?: string; noteId?: string }) => {
    setContextState(newContext);
  }, []);

  const clearContext = useCallback(() => {
    setContextState({});
  }, []);

  const value = useMemo(() => ({
    ...context,
    setContext,
    clearContext,
  }), [context, setContext, clearContext]);

  return (
    <PageContext.Provider value={value}>
      {children}
    </PageContext.Provider>
  );
};

export const usePageContext = () => {
  const context = useContext(PageContext);
  if (!context) {
    throw new Error('usePageContext must be used within PageContextProvider');
  }
  return context;
};

