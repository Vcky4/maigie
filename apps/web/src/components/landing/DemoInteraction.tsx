/*
 * Maigie - Your Intelligent Study Companion
 * Copyright (C) 2025 Maigie
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Monitor, Smartphone, Play, Send, Bot, User, BookOpen, Calendar, 
  Wifi, Battery, Signal, Target, Library, Mic, ChevronRight,
  Layout, Search
} from 'lucide-react';
import { cn } from '../../lib/utils';

type ViewMode = 'desktop' | 'mobile';
type Tab = 'dashboard' | 'goals' | 'calendar' | 'resources';
type CalendarView = 'month' | 'week' | 'day';
type VoiceStatus = 'idle' | 'listening' | 'speaking';

interface DemoInteractionProps {
  isActive: boolean;
  onStart: () => void;
}

interface Message {
  role: 'user' | 'ai';
  text: string;
}

interface Course {
  id: number;
  title: string;
  progress: number;
  color?: string;
}

interface Goal {
  id: number;
  title: string;
  deadline: string;
  progress: number;
}

interface Resource {
  id: number;
  type: 'video' | 'book' | 'article';
  title: string;
  author?: string;
  duration?: string;
}

interface WalkthroughState {
  activeTab: Tab;
  calendarView: CalendarView;
  voiceActive: boolean;
  voiceStatus: VoiceStatus;
  voiceText: string;
  goals: Goal[];
  resources: Resource[];
  courses: Course[];
}

const INITIAL_COURSES: Course[] = [
    { id: 101, title: "Calculus I", progress: 45, color: "bg-blue-500" },
    { id: 102, title: "World History", progress: 78, color: "bg-amber-500" }
];

const INITIAL_GOALS: Goal[] = [
    { id: 201, title: "Complete Calculus Problem Set", deadline: "Today", progress: 30 },
    { id: 202, title: "Read History Chapter 4", deadline: "Tomorrow", progress: 0 }
];

const INITIAL_RESOURCES: Resource[] = [
    { id: 301, type: 'book', title: "Calculus: Early Transcendentals", author: "Stewart" },
    { id: 302, type: 'video', title: "History of the 20th Century", duration: "45 min" }
];

export function DemoInteraction({ isActive, onStart }: DemoInteractionProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('desktop');
  
  // Common State
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");

  // Walkthrough State
  const [state, setState] = useState<WalkthroughState>({
    activeTab: 'dashboard',
    calendarView: 'month',
    voiceActive: false,
    voiceStatus: 'idle',
    voiceText: "",
    goals: INITIAL_GOALS,
    resources: INITIAL_RESOURCES,
    courses: INITIAL_COURSES
  });
  
  // Animation sequence
  useEffect(() => {
    if (!isActive) return;

    let isCancelled = false;

    const sequence = async () => {
      while (!isCancelled) {
        // --- RESET ---
        setMessages([]);
        setInputValue("");
        setState({
          activeTab: 'dashboard',
          calendarView: 'month',
          voiceActive: false,
          voiceStatus: 'idle',
          voiceText: "",
          goals: INITIAL_GOALS,
          resources: INITIAL_RESOURCES,
          courses: INITIAL_COURSES
        });

        // --- STEP 1: DASHBOARD & CHAT ---
        // Wait for user to "see" the empty state
        await new Promise(r => setTimeout(r, 2000));
        if (isCancelled) break;
        
        const userText = "Help me plan a study schedule for Physics.";
        for (let i = 0; i <= userText.length; i++) {
          if (isCancelled) break;
          setInputValue(userText.slice(0, i));
          await new Promise(r => setTimeout(r, 40));
        }
        if (isCancelled) break;
        
        await new Promise(r => setTimeout(r, 300));
        setMessages([{ role: 'user', text: userText }]);
        setInputValue("");
        
        await new Promise(r => setTimeout(r, 1000));
        if (isCancelled) break;
        setMessages(prev => [...prev, { role: 'ai', text: "I've created a Physics 101 course and added it to your dashboard." }]);
        setState(prev => ({
            ...prev,
            courses: [...INITIAL_COURSES, { id: 1, title: "Physics 101", progress: 0, color: "bg-primary" }]
        }));
        
        // --- STEP 2: VOICE INTERACTION (REALTIME) ---
        await new Promise(r => setTimeout(r, 2500));
        if (isCancelled) break;
        
        // Open Voice Mode
        setState(prev => ({ ...prev, voiceActive: true, voiceStatus: 'listening' }));
        
        const voiceCmd = "Create a goal to finish Chapter 1 by Friday.";
        // Simulate typing as speech recognition
        for (let i = 0; i <= voiceCmd.length; i++) {
            if (isCancelled) break;
            setState(prev => ({ ...prev, voiceText: voiceCmd.slice(0, i) }));
            await new Promise(r => setTimeout(r, 50));
        }
        
        await new Promise(r => setTimeout(r, 600));
        // AI Speaking back
        setState(prev => ({ ...prev, voiceStatus: 'speaking', voiceText: "Sure, creating that goal for you now." }));
        
        await new Promise(r => setTimeout(r, 2000));
        // Close Voice Mode
        setState(prev => ({ ...prev, voiceActive: false, voiceText: "", voiceStatus: 'idle' }));
        
        // --- STEP 3: NAVIGATE TO GOALS ---
        await new Promise(r => setTimeout(r, 500));
        setState(prev => ({ 
            ...prev, 
            activeTab: 'goals',
            goals: [{ id: 1, title: "Finish Chapter 1 (Physics)", deadline: "Friday", progress: 0 }, ...INITIAL_GOALS]
        }));

        // --- STEP 4: NAVIGATE TO CALENDAR ---
        await new Promise(r => setTimeout(r, 3000));
        setState(prev => ({ ...prev, activeTab: 'calendar' }));
        
        await new Promise(r => setTimeout(r, 1000));
        setState(prev => ({ ...prev, calendarView: 'week' })); // Switch view

        // --- STEP 5: NAVIGATE TO RESOURCES ---
        await new Promise(r => setTimeout(r, 3000));
        setState(prev => ({ 
            ...prev, 
            activeTab: 'resources',
            resources: [
                { id: 1, type: 'video', title: "Physics: Kinematics Basics", duration: "15 min" },
                { id: 2, type: 'book', title: "Fundamentals of Physics", author: "Halliday" },
                ...INITIAL_RESOURCES
            ]
        }));

        // --- LOOP ---
        await new Promise(r => setTimeout(r, 5000));
      }
    };

    sequence();

    return () => {
      isCancelled = true;
    };
  }, [isActive]);

  return (
    <div className="relative w-full h-full min-h-[400px] lg:min-h-[600px] bg-gray-50 rounded-xl overflow-hidden border border-gray-200 shadow-2xl">
      {/* View Switcher - Desktop Only */}
      <div className="absolute top-4 right-4 z-30 hidden md:flex bg-white/90 backdrop-blur rounded-lg p-1 shadow-sm border border-gray-200">
        <button
          onClick={() => setViewMode('desktop')}
          className={cn(
            "p-2 rounded-md transition-all",
            viewMode === 'desktop' ? "bg-primary text-white shadow-sm" : "text-gray-500 hover:text-gray-900"
          )}
          title="Desktop View"
        >
          <Monitor size={20} />
        </button>
        <button
          onClick={() => setViewMode('mobile')}
          className={cn(
            "p-2 rounded-md transition-all",
            viewMode === 'mobile' ? "bg-primary text-white shadow-sm" : "text-gray-500 hover:text-gray-900"
          )}
          title="Mobile View"
        >
          <Smartphone size={20} />
        </button>
      </div>

      {!isActive ? (
        /* Start Screen / Placeholder */
        <div 
            onClick={onStart}
            className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-gradient-to-br from-gray-50 to-indigo-50/50 cursor-pointer group transition-colors hover:bg-indigo-50/80"
        >
            <motion.div 
                whileHover={{ scale: 1.1 }}
                whileTap={{ scale: 0.95 }}
                className="w-24 h-24 bg-white rounded-full flex items-center justify-center shadow-xl mb-6 group-hover:shadow-2xl transition-all"
            >
                <Play className="text-primary ml-1" size={40} fill="currentColor" />
            </motion.div>
            <p className="text-gray-600 font-medium text-lg group-hover:text-primary transition-colors">See Maigie in Action</p>
            
            {/* Decorative Background Elements */}
            <div className="absolute top-10 left-10 w-32 h-32 bg-primary/5 rounded-full blur-2xl" />
            <div className="absolute bottom-10 right-10 w-40 h-40 bg-secondary/5 rounded-full blur-2xl" />
        </div>
      ) : (
        /* Active Demo Content */
        <div className="w-full h-full bg-slate-100 flex items-center justify-center p-4 md:p-8">
            <AnimatePresence mode="wait">
                {viewMode === 'desktop' ? (
                    <DesktopFrame 
                        key="desktop" 
                        state={state}
                        messages={messages} 
                        inputValue={inputValue} 
                    />
                ) : (
                    <MobileFrame 
                        key="mobile" 
                        state={state} 
                        messages={messages} 
                        inputValue={inputValue} 
                    />
                )}
            </AnimatePresence>
        </div>
      )}
    </div>
  );
}

function DesktopFrame({ state, messages, inputValue }: { state: WalkthroughState, messages: Message[], inputValue: string }) {
    return (
        <motion.div 
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.3 }}
            className="w-full max-w-6xl aspect-[16/10] bg-white rounded-lg shadow-2xl overflow-hidden flex flex-col border border-gray-200 relative"
        >
            {/* Voice Overlay */}
            <AnimatePresence>
                {state.voiceActive && (
                    <VoiceOverlay status={state.voiceStatus} text={state.voiceText} isMobile={false} />
                )}
            </AnimatePresence>

            {/* Browser Header */}
            <div className="h-8 bg-gray-50 border-b border-gray-200 flex items-center px-4 space-x-2">
                <div className="w-3 h-3 rounded-full bg-red-400" />
                <div className="w-3 h-3 rounded-full bg-amber-400" />
                <div className="w-3 h-3 rounded-full bg-green-400" />
                <div className="ml-4 flex-1 bg-white h-5 rounded border border-gray-200 text-[10px] flex items-center px-2 text-gray-400">
                    maigie.com/{state.activeTab}
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden">
                {/* Sidebar */}
                <div className="w-16 md:w-56 bg-gray-50 border-r border-gray-100 flex flex-col py-6">
                   {/* Logo Image */}
                   <img src="/assets/logo.png" alt="Maigie Logo" className="px-4 w-32 mb-8" />
                   <div className="space-y-1 px-3">
                       <SidebarItem icon={<Layout size={18} />} label="Dashboard" isActive={state.activeTab === 'dashboard'} />
                       <SidebarItem icon={<Target size={18} />} label="Goals" isActive={state.activeTab === 'goals'} />
                       <SidebarItem icon={<Calendar size={18} />} label="Schedule" isActive={state.activeTab === 'calendar'} />
                       <SidebarItem icon={<BookOpen size={18} />} label="Courses" isActive={false} />
                       <SidebarItem icon={<Library size={18} />} label="Resources" isActive={state.activeTab === 'resources'} />
                   </div>
                </div>

                {/* Main Content */}
                <div className="flex-1 flex bg-white">
                    <AnimatePresence mode="wait">
                        {state.activeTab === 'dashboard' && (
                            <DashboardView key="dashboard" messages={messages} inputValue={inputValue} courses={state.courses} />
                        )}
                        {state.activeTab === 'goals' && (
                            <GoalsView key="goals" goals={state.goals} />
                        )}
                        {state.activeTab === 'calendar' && (
                            <CalendarViewUI key="calendar" view={state.calendarView} />
                        )}
                        {state.activeTab === 'resources' && (
                            <ResourcesView key="resources" resources={state.resources} />
                        )}
                    </AnimatePresence>
                </div>
            </div>
        </motion.div>
    );
}

function MobileFrame({ state, messages, inputValue }: { state: WalkthroughState, messages: Message[], inputValue: string }) {
    return (
        <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ duration: 0.3 }}
            className="relative w-[300px] h-[600px] bg-gray-900 rounded-[2.5rem] border-[8px] border-gray-900 shadow-2xl overflow-hidden ring-1 ring-white/20"
        >
            {/* Voice Overlay (Mobile) */}
            <AnimatePresence>
                {state.voiceActive && (
                    <VoiceOverlay status={state.voiceStatus} text={state.voiceText} isMobile={true} />
                )}
            </AnimatePresence>

            {/* Status Bar */}
            <div className="h-6 bg-primary flex justify-between items-center px-4 text-[10px] text-white">
                <span>9:41</span>
                <div className="flex space-x-1">
                    <Signal size={10} />
                    <Wifi size={10} />
                    <Battery size={10} />
                </div>
            </div>
            
            {/* App Header (changes based on tab) */}
            <div className="bg-primary p-4 pb-4 rounded-b-2xl shadow-lg z-10 relative transition-all duration-300">
                <div className="flex justify-between items-center text-white mb-4">
                    <Bot size={24} />
                    <span className="font-bold text-lg capitalize">{state.activeTab}</span>
                    <div className="w-8 h-8 bg-white/20 rounded-full flex items-center justify-center">
                        <User size={16} />
                    </div>
                </div>
            </div>

            {/* Scrollable Content */}
            <div className="h-full bg-gray-50 -mt-4 pt-4 pb-20 flex flex-col overflow-hidden">
                <AnimatePresence mode="wait">
                    {state.activeTab === 'dashboard' && (
                        <div className="flex-1 flex flex-col overflow-hidden">
                            {/* Mobile Chat Area */}
                            <div className="flex-1 overflow-y-auto px-4 space-y-3 pt-2">
                                {messages.length === 0 ? (
                                    <ChatEmptyState />
                                ) : (
                                    messages.map((msg, idx) => (
                                        <motion.div 
                                            key={idx}
                                            initial={{ opacity: 0, scale: 0.9 }}
                                            animate={{ opacity: 1, scale: 1 }}
                                            className={cn(
                                                "p-3 rounded-2xl text-xs max-w-[85%]",
                                                msg.role === 'user' 
                                                    ? "bg-primary text-white ml-auto rounded-tr-sm" 
                                                    : "bg-white text-gray-800 mr-auto rounded-tl-sm shadow-sm"
                                            )}
                                        >
                                            {msg.text}
                                        </motion.div>
                                    ))
                                )}
                            </div>
                            {/* Mobile Input */}
                            <div className="bg-white border-t border-gray-100 p-3 z-20">
                                <div className="bg-gray-100 rounded-full px-4 py-2 flex items-center">
                                    <input 
                                        type="text" 
                                        value={inputValue} 
                                        readOnly
                                        placeholder="Message..." 
                                        className="bg-transparent flex-1 outline-none text-xs text-gray-800"
                                    />
                                    <div className="w-6 h-6 bg-primary rounded-full flex items-center justify-center text-white ml-2">
                                        <Send size={12} />
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                    {state.activeTab === 'goals' && (
                        <div className="p-4 space-y-3 overflow-y-auto">
                            {state.goals.map((goal, i) => (
                                <div key={i} className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
                                    <div className="text-xs font-bold text-gray-800 mb-1">{goal.title}</div>
                                    <div className="text-[10px] text-gray-500 mb-2">Due {goal.deadline}</div>
                                    <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                                        <div className="bg-green-500 h-full" style={{ width: `${goal.progress}%` }} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                    {/* Add other mobile tabs if needed, but focusing on chat/goals for demo loop */}
                </AnimatePresence>
            </div>

            {/* Bottom Nav */}
            <div className="absolute bottom-0 w-full bg-white border-t border-gray-100 h-16 flex justify-around items-center px-2 pb-2 z-20">
                {(['dashboard', 'goals', 'calendar', 'resources'] as const).map(tab => (
                    <div 
                        key={tab}
                        className={cn(
                            "flex flex-col items-center justify-center w-12 h-12 rounded-full transition-colors",
                            state.activeTab === tab ? "text-primary" : "text-gray-400"
                        )}
                    >
                        {tab === 'dashboard' && <Layout size={20} />}
                        {tab === 'goals' && <Target size={20} />}
                        {tab === 'calendar' && <Calendar size={20} />}
                        {tab === 'resources' && <Library size={20} />}
                    </div>
                ))}
            </div>
        </motion.div>
    );
}

// --- SHARED UI COMPONENTS ---

function VoiceOverlay({ status, text, isMobile }: { status: VoiceStatus, text: string, isMobile: boolean }) {
    return (
        <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            className={cn(
                "absolute z-50 flex flex-col items-center justify-center shadow-2xl border border-white/10 backdrop-blur-md text-white",
                isMobile 
                    ? "inset-0 bg-black/90" 
                    : "bottom-8 left-1/2 -translate-x-1/2 bg-black/80 rounded-3xl px-8 py-6 min-w-[350px]"
            )}
        >
            {/* Visualizer */}
            <div className="flex items-center justify-center space-x-1.5 mb-6 h-12">
                {[...Array(5)].map((_, i) => (
                    <motion.div
                        key={i}
                        className={cn("w-2 rounded-full", status === 'speaking' ? 'bg-blue-400' : 'bg-primary')}
                        animate={{ 
                            height: status !== 'idle' ? [10, 32, 10] : 10,
                            opacity: status !== 'idle' ? 1 : 0.5 
                        }}
                        transition={{ 
                            repeat: Infinity, 
                            duration: 0.8, 
                            delay: i * 0.1,
                            ease: "easeInOut" 
                        }}
                    />
                ))}
            </div>

            <div className="text-center space-y-2">
                <p className="text-lg font-semibold">
                    {status === 'listening' ? "Listening..." : status === 'speaking' ? "Maigie Speaking..." : "Tap to speak"}
                </p>
                <p className="text-sm text-gray-300 max-w-xs mx-auto h-10 flex items-center justify-center">
                    "{text}"
                </p>
            </div>

            {/* Mic Button Visual */}
            <div className={cn(
                "mt-6 rounded-full flex items-center justify-center transition-all duration-300",
                status === 'listening' ? "w-20 h-20 bg-red-500 shadow-red-500/50 shadow-lg animate-pulse" : "w-16 h-16 bg-primary shadow-lg"
            )}>
                <Mic size={28} fill="currentColor" />
            </div>
        </motion.div>
    );
}

function ChatEmptyState() {
    return (
        <div className="flex flex-col items-center justify-center h-full p-4 text-center">
            <div className="w-16 h-16 bg-indigo-50 rounded-full flex items-center justify-center mb-4">
                <Bot size={32} className="text-primary" />
            </div>
            <h3 className="text-gray-800 font-bold mb-2">What would you like to do today?</h3>
            <p className="text-gray-500 text-sm mb-6">I can help you plan courses, quiz you, or find resources.</p>
            <div className="flex flex-wrap justify-center gap-2">
                {["Create a course", "Quiz me", "Plan my week"].map((chip, i) => (
                    <span key={i} className="bg-white border border-gray-200 text-gray-600 text-xs px-3 py-1.5 rounded-full shadow-sm">
                        {chip}
                    </span>
                ))}
            </div>
        </div>
    );
}

function SidebarItem({ icon, label, isActive }: { icon: React.ReactNode, label: string, isActive: boolean }) {
    return (
        <div className={cn(
            "p-2.5 rounded-lg flex items-center text-sm font-medium transition-colors cursor-default",
            isActive ? "bg-primary/10 text-primary" : "text-gray-600 hover:bg-gray-100"
        )}>
            <span className={isActive ? "text-primary" : "text-gray-500"}>{icon}</span>
            <span className="ml-3 hidden md:inline">{label}</span>
        </div>
    );
}

// --- VIEW COMPONENTS ---

function DashboardView({ messages, inputValue, courses }: { messages: Message[], inputValue: string, courses: Course[] }) {
    return (
        <motion.div 
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex-1 flex"
        >
            {/* Dashboard Content */}
            <div className="flex-1 p-8 overflow-y-auto">
                <h2 className="text-2xl font-bold text-gray-800 mb-6">Good Morning, Victor</h2>
                <div className="grid grid-cols-1 gap-6">
                    {/* Stats Row */}
                    <div className="grid grid-cols-3 gap-4 mb-4">
                        <div className="p-4 bg-blue-50 rounded-xl border border-blue-100">
                            <div className="text-blue-600 text-xs font-bold uppercase mb-1">Study Time</div>
                            <div className="text-2xl font-bold text-gray-900">4.2h</div>
                        </div>
                        <div className="p-4 bg-green-50 rounded-xl border border-green-100">
                            <div className="text-green-600 text-xs font-bold uppercase mb-1">Tasks</div>
                            <div className="text-2xl font-bold text-gray-900">8/10</div>
                        </div>
                        <div className="p-4 bg-purple-50 rounded-xl border border-purple-100">
                            <div className="text-purple-600 text-xs font-bold uppercase mb-1">Streak</div>
                            <div className="text-2xl font-bold text-gray-900">12 Days</div>
                        </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl border border-gray-100 shadow-sm">
                        <div className="flex justify-between mb-4">
                            <h3 className="font-semibold text-gray-700">Active Courses</h3>
                        </div>
                        <div className="space-y-4">
                            {courses.map((course, i) => (
                                <motion.div 
                                    key={i}
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    className="p-4 bg-white hover:bg-gray-50 rounded-lg border border-gray-100 flex justify-between items-center transition-colors"
                                >
                                    <div className="flex-1 mr-4">
                                        <div className="flex justify-between items-center mb-1">
                                            <div className="font-bold text-gray-800">{course.title}</div>
                                            <div className="text-xs font-bold text-primary">{course.progress}%</div>
                                        </div>
                                        <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                                            <div className={cn("h-full", course.color || "bg-primary")} style={{ width: `${course.progress}%` }} />
                                        </div>
                                    </div>
                                    <div className="h-8 w-8 bg-gray-100 rounded-full flex items-center justify-center text-gray-400">
                                        <ChevronRight size={16} />
                                    </div>
                                </motion.div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* Chat Panel */}
            <div className="w-80 bg-white border-l border-gray-200 flex flex-col">
                <div className="p-4 border-b border-gray-100 flex items-center justify-between">
                    <span className="font-semibold text-gray-700">Maigie Chat</span>
                    <Bot size={18} className="text-primary" />
                </div>
                <div className="flex-1 p-4 space-y-4 overflow-y-auto bg-gray-50/50 flex flex-col">
                    {messages.length === 0 ? (
                        <ChatEmptyState />
                    ) : (
                        messages.map((msg, idx) => (
                            <div key={idx} className={cn("p-3 rounded-lg text-sm max-w-[90%]", msg.role === 'user' ? "bg-primary text-white ml-auto rounded-tr-none" : "bg-white border border-gray-200 text-gray-700 mr-auto rounded-tl-none")}>
                                {msg.text}
                            </div>
                        ))
                    )}
                </div>
                <div className="p-4 border-t border-gray-100">
                    <div className="flex items-center bg-gray-100 rounded-full px-4 py-2">
                        <input type="text" value={inputValue} readOnly placeholder="Ask Maigie..." className="bg-transparent flex-1 outline-none text-sm" />
                        <div className="w-8 h-8 bg-primary rounded-full flex items-center justify-center text-white ml-2 shadow-sm">
                            <Send size={14} />
                        </div>
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

function GoalsView({ goals }: { goals: Goal[] }) {
    return (
        <motion.div 
            initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}
            className="flex-1 p-8 bg-gray-50/30 overflow-y-auto"
        >
            <div className="flex justify-between items-center mb-8">
                <h2 className="text-2xl font-bold text-gray-800">Your Goals</h2>
                <button className="bg-primary text-white px-4 py-2 rounded-lg text-sm font-medium shadow-sm">
                    + New Goal
                </button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {goals.map((goal, i) => (
                    <motion.div 
                        key={i}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        className="bg-white p-6 rounded-xl border border-gray-100 shadow-sm hover:shadow-md transition-shadow"
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div className="p-2 bg-green-100 text-green-600 rounded-lg">
                                <Target size={20} />
                            </div>
                            <span className="text-xs font-medium bg-gray-100 px-2 py-1 rounded text-gray-600">
                                Due {goal.deadline}
                            </span>
                        </div>
                        <h3 className="font-bold text-lg text-gray-800 mb-2">{goal.title}</h3>
                        <div className="flex items-center space-x-3">
                            <div className="flex-1 bg-gray-100 h-2 rounded-full overflow-hidden">
                                <div className="bg-green-500 h-full" style={{ width: `${goal.progress}%` }} />
                            </div>
                            <span className="text-xs text-gray-500 font-medium">{goal.progress}%</span>
                        </div>
                    </motion.div>
                ))}
            </div>
        </motion.div>
    );
}

function CalendarViewUI({ view }: { view: CalendarView }) {
    return (
        <motion.div 
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex-1 p-8 overflow-y-auto"
        >
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-bold text-gray-800">Calendar</h2>
                <div className="flex bg-gray-100 p-1 rounded-lg">
                    {(['month', 'week', 'day'] as const).map(v => (
                        <div 
                            key={v}
                            className={cn(
                                "px-4 py-1.5 rounded-md text-sm font-medium capitalize transition-all",
                                view === v ? "bg-white text-primary shadow-sm" : "text-gray-500"
                            )}
                        >
                            {v}
                        </div>
                    ))}
                </div>
            </div>
            
            <div className="bg-white border border-gray-200 rounded-xl shadow-sm h-[400px] p-6 relative overflow-hidden">
                {view === 'month' && (
                    <div className="grid grid-cols-7 gap-4 h-full">
                        {/* Mock Month Grid */}
                        {Array.from({ length: 35 }).map((_, i) => (
                            <div key={i} className={cn(
                                "border-t border-gray-100 pt-2 text-sm text-gray-500",
                                i === 14 ? "bg-blue-50 text-blue-600 font-bold rounded p-1" : ""
                            )}>
                                {i + 1 <= 31 ? i + 1 : ""}
                                {i === 14 && <div className="mt-1 text-[10px] bg-blue-100 p-1 rounded truncate">Physics Exam</div>}
                            </div>
                        ))}
                    </div>
                )}
                {view === 'week' && (
                    <motion.div 
                        initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}
                        className="grid grid-cols-5 gap-4 h-full"
                    >
                        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].map((day, i) => (
                            <div key={i} className="border-r border-gray-100 last:border-0 h-full relative">
                                <div className="text-center font-medium text-gray-600 mb-4">{day}</div>
                                {i === 4 && (
                                    <motion.div 
                                        initial={{ scale: 0.8, opacity: 0 }}
                                        animate={{ scale: 1, opacity: 1 }}
                                        className="absolute top-1/3 left-1 right-1 bg-green-100 border border-green-200 text-green-800 p-2 rounded text-xs"
                                    >
                                        <div className="font-bold">Finish Chapter 1</div>
                                        <div>2:00 PM</div>
                                    </motion.div>
                                )}
                            </div>
                        ))}
                    </motion.div>
                )}
            </div>
        </motion.div>
    );
}

function ResourcesView({ resources }: { resources: Resource[] }) {
    return (
        <motion.div 
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex-1 p-8 overflow-y-auto"
        >
            <div className="flex justify-between items-center mb-8">
                <h2 className="text-2xl font-bold text-gray-800">Recommended Resources</h2>
                <div className="relative">
                    <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                    <input type="text" placeholder="Search..." className="pl-10 pr-4 py-2 border border-gray-200 rounded-lg text-sm outline-none focus:border-primary" />
                </div>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {resources.map((res, i) => (
                    <motion.div 
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.1 }}
                        className="flex bg-white p-4 rounded-xl border border-gray-100 shadow-sm hover:shadow-md"
                    >
                        <div className={cn("w-20 h-24 rounded-lg flex items-center justify-center mr-4", res.type === 'video' ? 'bg-red-100 text-red-500' : 'bg-blue-100 text-blue-500')}>
                            {res.type === 'video' ? <Play size={24} /> : <BookOpen size={24} />}
                        </div>
                        <div className="flex-1">
                            <div className="text-xs uppercase font-bold text-gray-400 mb-1">{res.type}</div>
                            <h3 className="font-bold text-gray-800 mb-1">{res.title}</h3>
                            <p className="text-sm text-gray-500 mb-3">{res.author || res.duration}</p>
                            <div className="flex space-x-2">
                                <span className="text-[10px] bg-gray-100 px-2 py-1 rounded text-gray-600">Physics</span>
                                <span className="text-[10px] bg-green-50 text-green-600 px-2 py-1 rounded font-medium">Recommended</span>
                            </div>
                        </div>
                    </motion.div>
                ))}
            </div>
        </motion.div>
    );
}
