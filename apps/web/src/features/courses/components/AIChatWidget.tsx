import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Send, 
  Mic, 
  Sparkles, 
  StopCircle, 
  ChevronDown, 
  ChevronUp,
  Image,
  AtSign,
  FileText,
  Layout,
  Hash,
  RotateCcw
} from 'lucide-react';
import { useLocation, useParams } from 'react-router-dom';
import { cn } from '../../../lib/utils';
import { ChatWebSocketClient, chatApi } from '../services/chatApi';
import { useAuthStore } from '../../../features/auth/store/authStore';
import ReactMarkdownOriginal from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Workaround for React 18 type definition mismatch
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ReactMarkdown = ReactMarkdownOriginal as any;

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
}

export const AIChatWidget = () => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  
  // New states for popovers
  const [isMentionMenuOpen, setIsMentionMenuOpen] = useState(false);
  const [isContextPickerOpen, setIsContextPickerOpen] = useState(false);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wsClientRef = useRef<ChatWebSocketClient | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamingTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const isStoppedRef = useRef<boolean>(false);
  const location = useLocation();
  const params = useParams<{ id?: string; courseId?: string; topicId?: string; noteId?: string }>();
  const { isAuthenticated } = useAuthStore();

  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'assistant',
      content: "Hi! I'm Maigie. I can help you with whatever you're looking at right now. Ask me anything!",
      timestamp: new Date()
    }
  ]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (isExpanded) {
      scrollToBottom();
    }
  }, [messages, isExpanded, isTyping]);

  // Auto-scroll during streaming
  useEffect(() => {
    if (isExpanded && streamingMessageIdRef.current) {
      scrollToBottom();
    }
  }, [messages.find(msg => msg.id === streamingMessageIdRef.current)?.content, isExpanded]);

  const getPageContext = () => {
    const pageName = location.pathname === '/dashboard' ? 'Dashboard' : 
      location.pathname.split('/').filter(Boolean).pop()?.replace(/-/g, ' ') || 'Page';
    return pageName.charAt(0).toUpperCase() + pageName.slice(1);
  };

  const getContextForMessage = () => {
    const context: Record<string, any> = {
      pageContext: getPageContext()
    };

    // Extract course, topic, and note IDs from URL params
    const courseId = params.id || params.courseId;
    const topicId = params.topicId;
    const noteId = params.noteId;

    if (courseId) {
      context.courseId = courseId;
    }

    if (topicId) {
      context.topicId = topicId;
    }

    if (noteId) {
      context.noteId = noteId;
    }

    return Object.keys(context).length > 1 ? context : null; // Only return if we have more than just pageContext
  };

  const handleSendMessage = () => {
    if (!inputValue.trim() || !wsClientRef.current?.isConnected()) return;

    const userMessageContent = inputValue;

    const newMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: userMessageContent,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, newMessage]);
    setInputValue('');
    setIsTyping(true);
    
    if (!isExpanded) {
      setIsExpanded(true);
    }

    // Get context and send message via WebSocket
    const context = getContextForMessage();
    wsClientRef.current?.send(userMessageContent, context || undefined);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const toggleVoice = async () => {
    if (!isListening) {
      // Start recording
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
        };

        mediaRecorder.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
          const audioFile = new File([audioBlob], 'recording.webm', { type: 'audio/webm' });
          
          try {
            setIsTyping(true);
            const result = await chatApi.transcribeVoice(audioFile);
            setInputValue(result.text);
            setIsListening(false);
            setIsTyping(false);
            inputRef.current?.focus();
          } catch (error) {
            console.error('Transcription error:', error);
            setIsListening(false);
            setIsTyping(false);
          }

          // Stop all tracks
          stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start();
        setIsListening(true);
      } catch (error) {
        console.error('Error accessing microphone:', error);
        alert('Could not access microphone. Please check permissions.');
      }
    } else {
      // Stop recording
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
    }
  };

  const handleMentionClick = () => {
    setIsMentionMenuOpen(!isMentionMenuOpen);
    setIsContextPickerOpen(false);
  };

  const handleContextClick = () => {
    setIsContextPickerOpen(!isContextPickerOpen);
    setIsMentionMenuOpen(false);
  };

  const insertMention = (text: string) => {
    setInputValue(prev => prev + (prev.endsWith(' ') ? '' : ' ') + `@${text} `);
    setIsMentionMenuOpen(false);
    inputRef.current?.focus();
  };

  // Stream text character by character
  const streamText = (fullText: string, messageId: string) => {
    let currentIndex = 0;
    const streamingSpeed = 15; // milliseconds per character (adjust for speed)
    const isStoppedRef = { current: false };

    const stream = () => {
      if (isStoppedRef.current) {
        // Streaming was stopped
        setMessages(prev => 
          prev.map(msg => 
            msg.id === messageId 
              ? { ...msg, isStreaming: false }
              : msg
          )
        );
        setIsTyping(false);
        streamingMessageIdRef.current = null;
        return;
      }

      if (currentIndex < fullText.length) {
        const partialText = fullText.substring(0, currentIndex + 1);
        
        setMessages(prev => {
          const updated = prev.map(msg => 
            msg.id === messageId 
              ? { ...msg, content: partialText, isStreaming: true }
              : msg
          );
          return updated;
        });

        currentIndex++;
        streamingTimeoutRef.current = setTimeout(stream, streamingSpeed);
        
        // Auto-scroll during streaming
        if (isExpanded) {
          setTimeout(() => scrollToBottom(), 0);
        }
      } else {
        // Streaming complete
        setMessages(prev => 
          prev.map(msg => 
            msg.id === messageId 
              ? { ...msg, isStreaming: false }
              : msg
          )
        );
        setIsTyping(false);
        streamingMessageIdRef.current = null;
        scrollToBottom();
      }
    };

    // Store stop function in a way we can access it
    (streamingTimeoutRef as any).stop = () => {
      isStoppedRef.current = true;
      if (streamingTimeoutRef.current) {
        clearTimeout(streamingTimeoutRef.current);
        streamingTimeoutRef.current = null;
      }
    };

    stream();
  };

  const handleStopGeneration = () => {
    // Mark as stopped
    isStoppedRef.current = true;
    
    // Stop the streaming animation
    if (streamingTimeoutRef.current) {
      clearTimeout(streamingTimeoutRef.current);
      streamingTimeoutRef.current = null;
    }
    
    // Stop the streaming state
    if (streamingMessageIdRef.current) {
      setMessages(prev => 
        prev.map(msg => 
          msg.id === streamingMessageIdRef.current 
            ? { ...msg, isStreaming: false }
            : msg
        )
      );
      streamingMessageIdRef.current = null;
    }
    
    setIsTyping(false);
  };

  const handleUndo = () => {
    // Remove the last user message and its corresponding assistant response
    setMessages(prev => {
      if (prev.length <= 1) {
        // Keep at least the initial welcome message
        return prev;
      }
      
      const newMessages = [...prev];
      
      // Find the last user message index
      let lastUserIndex = -1;
      for (let i = newMessages.length - 1; i >= 0; i--) {
        if (newMessages[i].role === 'user') {
          lastUserIndex = i;
          break;
        }
      }
      
      if (lastUserIndex !== -1) {
        // Remove from the last user message to the end
        // This removes the user message and any assistant response after it
        newMessages.splice(lastUserIndex);
      }
      
      return newMessages;
    });
    
    // Stop any ongoing streaming
    handleStopGeneration();
  };

  // Check if undo is available (more than just the welcome message)
  const canUndo = messages.length > 1 && messages.some(msg => msg.role === 'user');

  // Initialize WebSocket connection
  useEffect(() => {
    if (!isAuthenticated) return;

    const handleMessage = (message: string) => {
      // If generation was stopped, ignore this message
      if (isStoppedRef.current) {
        isStoppedRef.current = false; // Reset for next message
        return;
      }
      
      setIsTyping(false); // Hide typing indicator, streaming will show the text
      const messageId = Date.now().toString();
      
      const aiResponse: Message = {
        id: messageId,
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isStreaming: true
      };
      
      setMessages(prev => [...prev, aiResponse]);
      streamingMessageIdRef.current = messageId;
      
      // Start streaming the text
      streamText(message, messageId);
    };

    const handleError = (error: Error) => {
      console.error('WebSocket error:', error);
      setIsTyping(false);
    };

    const handleConnect = () => {
      console.log('WebSocket connected');
    };

    const handleDisconnect = () => {
      console.log('WebSocket disconnected');
    };

    wsClientRef.current = new ChatWebSocketClient(
      handleMessage,
      handleError,
      handleConnect,
      handleDisconnect
    );

    wsClientRef.current.connect();

    // Handle action events (e.g., course created, note created)
    wsClientRef.current.on('event', (data: any) => {
      console.log('Action event:', data);
      
      // Dispatch a custom event that pages can listen to for refetching
      const event = new CustomEvent('aiActionCompleted', {
        detail: {
          action: data.action || data.type,
          status: data.status,
          payload: data
        }
      });
      window.dispatchEvent(event);
    });

    return () => {
      wsClientRef.current?.disconnect();
      // Stop any ongoing recording
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      // Clear streaming timeout
      if (streamingTimeoutRef.current) {
        clearTimeout(streamingTimeoutRef.current);
      }
    };
  }, [isAuthenticated]);

  // Close menus when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      // In a real implementation, we'd check if the click was inside the menu refs
      // For now, we'll rely on the buttons toggling it
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const currentContext = getPageContext();
  const placeholderText = messages.length > 1 ? "Add a follow-up..." : "Ask Maigie anything...";

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 flex flex-col items-center pointer-events-none pb-24 lg:pb-8 lg:pl-64">
      {/* Container for Chat and Input */}
      <div className="w-full max-w-2xl px-4 flex flex-col items-center pointer-events-auto relative">
        
        {/* Chat Conversation Area (Collapsible) */}
        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ opacity: 0, height: 0, y: 20 }}
              animate={{ opacity: 1, height: 'auto', y: 0 }}
              exit={{ opacity: 0, height: 0, y: 20 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="w-full bg-white rounded-xl shadow-2xl border border-gray-200 overflow-hidden mb-3"
              style={{ maxHeight: '60vh' }}
            >
              {/* Header */}
              <div className="bg-gray-50 border-b border-gray-100 px-4 py-3 flex items-center justify-between text-gray-500">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <img src="/assets/logo-s.png" alt="Maigie" className="w-5 h-5 object-contain" />
                  <span className="text-gray-900">Maigie Chat</span>
                </div>
                <button 
                  onClick={() => setIsExpanded(false)}
                  className="p-1 hover:bg-gray-200 rounded text-gray-400 hover:text-gray-900 transition-colors"
                >
                  <ChevronDown className="w-4 h-4" />
                </button>
              </div>

              {/* Messages */}
              <div className="overflow-y-auto p-4 space-y-6 bg-white min-h-[300px] max-h-[50vh]">
                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={cn(
                      "flex gap-4 max-w-[95%]",
                      msg.role === 'user' ? "ml-auto flex-row-reverse" : ""
                    )}
                  >
                    <div className={cn(
                      "w-8 h-8 rounded-full flex items-center justify-center shrink-0 border overflow-hidden",
                      msg.role === 'user' 
                        ? "bg-indigo-50 border-indigo-100 text-indigo-600" 
                        : "bg-white border-gray-200"
                    )}>
                      {msg.role === 'user' ? (
                        <div className="w-4 h-4 rounded-full bg-indigo-600" />
                      ) : (
                        <img src="/assets/logo-s.png" alt="AI" className="w-5 h-5 object-contain" />
                      )}
                    </div>
                    <div className={cn(
                      "text-sm leading-relaxed pt-1.5",
                      msg.role === 'user' ? "text-gray-900" : "text-gray-700"
                    )}>
                      {msg.role === 'assistant' ? (
                        <>
                          <ReactMarkdown 
                            remarkPlugins={[remarkGfm]}
                            components={{
                              p: ({ children }: any) => <p className="mb-2 last:mb-0">{children}</p>,
                              strong: ({ children }: any) => <strong className="font-semibold text-gray-900">{children}</strong>,
                              em: ({ children }: any) => <em className="italic">{children}</em>,
                              code: ({ inline, children }: any) => 
                                inline ? (
                                  <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-xs font-mono">{children}</code>
                                ) : (
                                  <code className="block bg-gray-100 text-gray-800 p-2 rounded text-xs font-mono overflow-x-auto">{children}</code>
                                ),
                              pre: ({ children }: any) => <pre className="bg-gray-100 p-2 rounded text-xs font-mono overflow-x-auto mb-2">{children}</pre>,
                              ul: ({ children }: any) => <ul className="list-disc list-inside mb-2 space-y-1">{children}</ul>,
                              ol: ({ children }: any) => <ol className="list-decimal list-inside mb-2 space-y-1">{children}</ol>,
                              li: ({ children }: any) => <li className="ml-2">{children}</li>,
                              h1: ({ children }: any) => <h1 className="text-lg font-bold mb-2 mt-3 first:mt-0">{children}</h1>,
                              h2: ({ children }: any) => <h2 className="text-base font-bold mb-2 mt-3 first:mt-0">{children}</h2>,
                              h3: ({ children }: any) => <h3 className="text-sm font-semibold mb-1 mt-2 first:mt-0">{children}</h3>,
                              blockquote: ({ children }: any) => <blockquote className="border-l-4 border-gray-300 pl-3 italic my-2">{children}</blockquote>,
                              a: ({ href, children }: any) => <a href={href} className="text-indigo-600 hover:text-indigo-800 underline" target="_blank" rel="noopener noreferrer">{children}</a>,
                              hr: () => <hr className="my-3 border-gray-200" />,
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                          {msg.isStreaming && (
                            <span 
                              className="inline-block w-0.5 h-4 bg-indigo-500 ml-0.5 align-middle"
                              style={{
                                animation: 'blink 1s ease-in-out infinite'
                              }}
                            />
                          )}
                        </>
                      ) : (
                        <>
                          {msg.content}
                        </>
                      )}
                    </div>
                  </div>
                ))}
                
                {isTyping && (
                  <div className="flex gap-4 max-w-[95%]">
                     <div className="w-8 h-8 rounded-full bg-white border border-gray-200 flex items-center justify-center shrink-0 overflow-hidden">
                        <img src="/assets/logo-s.png" alt="Thinking" className="w-5 h-5 object-contain opacity-50" />
                     </div>
                     <div className="flex gap-1 items-center pt-3 pl-1">
                        <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
                        <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
                        <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" />
                     </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Collapsed Chat Handle */}
        {!isExpanded && !isTyping && (
          <motion.div 
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            onClick={() => setIsExpanded(true)}
            className="absolute -top-10 left-0 right-0 h-9 bg-white rounded-t-lg border border-gray-200 border-b-0 flex items-center justify-between px-3 text-xs text-gray-500 z-0 mx-4 cursor-pointer hover:bg-gray-50 transition-colors shadow-sm"
          >
             <div className="flex items-center gap-2">
                <img src="/assets/logo-s.png" alt="Maigie" className="w-4 h-4 object-contain opacity-70" />
                <span className="font-medium">Maigie Chat</span>
             </div>
             <div className="flex gap-2">
                <ChevronUp className="w-3 h-3" />
             </div>
          </motion.div>
        )}
        
        {/* Context Picker Popover */}
        <AnimatePresence>
          {isContextPickerOpen && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.95 }}
              className="absolute bottom-full left-4 mb-2 w-48 bg-white rounded-lg shadow-xl border border-gray-200 overflow-hidden z-20"
            >
              <div className="p-1">
                <div className="px-2 py-1.5 text-xs font-semibold text-gray-400 uppercase">Switch Context</div>
                {['Global', 'Dashboard', 'Courses', 'Notes'].map((ctx) => (
                  <button
                    key={ctx}
                    onClick={() => {
                      // In real app, this would set context state
                      setIsContextPickerOpen(false);
                    }}
                    className={cn(
                       "w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-gray-100 transition-colors flex items-center gap-2",
                       currentContext === ctx ? "bg-indigo-50 text-indigo-600" : "text-gray-700"
                    )}
                  >
                    <Layout className="w-3.5 h-3.5" />
                    {ctx}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Mention Menu Popover */}
        <AnimatePresence>
          {isMentionMenuOpen && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.95 }}
              className="absolute bottom-full right-4 mb-2 w-56 bg-white rounded-lg shadow-xl border border-gray-200 overflow-hidden z-20"
            >
              <div className="p-1">
                <div className="px-2 py-1.5 text-xs font-semibold text-gray-400 uppercase">Mention</div>
                <button onClick={() => insertMention('Page')} className="w-full text-left px-2 py-1.5 text-sm text-gray-700 rounded-md hover:bg-gray-100 transition-colors flex items-center gap-2">
                  <FileText className="w-3.5 h-3.5" />
                  Current Page
                </button>
                <button onClick={() => insertMention('Selection')} className="w-full text-left px-2 py-1.5 text-sm text-gray-700 rounded-md hover:bg-gray-100 transition-colors flex items-center gap-2">
                  <Hash className="w-3.5 h-3.5" />
                  Current Selection
                </button>
                <div className="h-[1px] bg-gray-100 my-1" />
                <button onClick={() => insertMention('Course')} className="w-full text-left px-2 py-1.5 text-sm text-gray-700 rounded-md hover:bg-gray-100 transition-colors flex items-center gap-2">
                  <Layout className="w-3.5 h-3.5" />
                  Link to Course...
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Floating Input Bar (Light Theme) */}
        <div className="w-full relative group">
           {/* Generating Status Bar */}
           {(isTyping || streamingMessageIdRef.current) && (
             <motion.div 
               initial={{ opacity: 0, y: 10 }}
               animate={{ opacity: 1, y: 0 }}
               className="absolute -top-10 left-0 right-0 h-9 bg-gray-50 rounded-t-lg border border-gray-200 border-b-0 flex items-center justify-between px-3 text-xs text-gray-500 z-0 mx-4"
             >
                <div className="flex items-center gap-2">
                   <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse" />
                   <span>Generating...</span>
                </div>
                <div className="flex gap-2">
                   <button 
                     onClick={handleStopGeneration}
                     className="hover:text-gray-900 transition-colors"
                   >
                     Stop
                   </button>
                </div>
             </motion.div>
           )}

          <motion.div 
            layout
            className={cn(
              "w-full bg-white rounded-xl shadow-2xl border border-gray-200 flex flex-col transition-all duration-300 relative z-10",
              isExpanded ? "rounded-b-none border-b-0" : "hover:border-gray-300"
            )}
          >
            {/* Input Area */}
            <div className="relative w-full p-3">
               <textarea
                  ref={inputRef}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={placeholderText}
                  className="w-full bg-transparent border-0 focus:ring-0 focus:outline-none p-0 min-h-[24px] max-h-32 resize-none text-sm text-gray-900 placeholder:text-gray-400 outline-none leading-relaxed"
                  rows={1}
                  style={{
                    height: 'auto',
                    minHeight: '24px'
                  }} 
                />
            </div>

            {/* Toolbar */}
            <div className="flex items-center justify-between px-2 pb-2">
               {/* Left Controls (Context/Model) */}
               <div className="flex items-center gap-2">
                  <div 
                    onClick={handleContextClick}
                    className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 cursor-pointer transition-colors border border-transparent hover:border-gray-200 group/pill"
                  >
                     <Sparkles className="w-3.5 h-3.5 text-indigo-500" />
                     <span className="text-xs font-medium text-gray-500 group-hover/pill:text-gray-700 hidden sm:inline">Context: {currentContext}</span>
                     <span className="text-xs font-medium text-gray-500 group-hover/pill:text-gray-700 sm:hidden">{currentContext}</span>
                     <ChevronDown className="w-3 h-3 text-gray-400" />
                  </div>
                  
                  <div className="hidden sm:flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 cursor-pointer transition-colors border border-transparent hover:border-gray-200 group/pill">
                     <span className="text-xs font-medium text-gray-500 group-hover/pill:text-gray-700">Gemini 3 Pro</span>
                     <ChevronDown className="w-3 h-3 text-gray-400" />
                  </div>
               </div>

               {/* Right Controls (Actions) */}
               <div className="flex items-center gap-1">
                  <div className="h-4 w-[1px] bg-gray-200 mx-1 hidden sm:block" />
                  
                  {canUndo && (
                    <button 
                      onClick={handleUndo}
                      className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors hidden sm:block"
                      title="Undo last message"
                    >
                      <RotateCcw className="w-4 h-4" />
                    </button>
                  )}
                  
                  <button 
                    onClick={handleMentionClick}
                    className={cn(
                      "p-1.5 rounded hover:bg-gray-100 transition-colors hidden sm:block",
                      isMentionMenuOpen ? "text-indigo-600 bg-indigo-50" : "text-gray-400 hover:text-gray-600"
                    )} 
                    title="Mention"
                  >
                     <AtSign className="w-4 h-4" />
                  </button>
                  
                  <button className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors" title="Add Image">
                     <Image className="w-4 h-4" />
                  </button>
                  
                  <button 
                     onClick={toggleVoice}
                     className={cn(
                        "p-1.5 rounded hover:bg-gray-100 transition-colors ml-1",
                        isListening ? "text-red-500 animate-pulse" : "text-gray-400 hover:text-gray-600"
                     )}
                  >
                     <Mic className="w-4 h-4" />
                  </button>

                  <button
                    onClick={handleSendMessage}
                    disabled={!inputValue.trim() && !isListening}
                    className={cn(
                      "ml-1 p-1.5 rounded-lg flex items-center justify-center transition-all duration-200",
                      inputValue.trim()
                        ? "bg-indigo-600 text-white hover:bg-indigo-700"
                        : "bg-gray-100 text-gray-400 cursor-not-allowed"
                    )}
                  >
                     {isListening ? <StopCircle className="w-4 h-4" /> : <Send className="w-4 h-4" />}
                  </button>

                  {!isExpanded && messages.length > 1 && (
                     <button 
                        onClick={() => setIsExpanded(true)}
                        className="ml-1 p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 hidden sm:block"
                     >
                        <ChevronUp className="w-4 h-4" />
                     </button>
                  )}
               </div>
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
};
