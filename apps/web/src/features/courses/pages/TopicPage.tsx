import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import { notesApi } from '../../notes/services/notesApi';
import type { Course, Topic } from '../types/courses.types';
import type { Note } from '../../notes/types/notes.types';
import { ArrowLeft, CheckCircle, Circle, ChevronRight, ChevronLeft, BookOpen, Save, Check, Brain, FileText } from 'lucide-react';
import { cn } from '../../../lib/utils';

export const TopicPage = () => {
  const { courseId, moduleId, topicId } = useParams<{ courseId: string; moduleId: string; topicId: string }>();
  
  const [course, setCourse] = useState<Course | null>(null);
  const [currentTopic, setCurrentTopic] = useState<Topic | null>(null);
  const [currentNote, setCurrentNote] = useState<Note | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isCompleting, setIsCompleting] = useState(false);
  
  // Note content state
  const [content, setContent] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const savedIndicatorTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (courseId) {
      fetchCourse(courseId);
    }
  }, [courseId]);

  useEffect(() => {
    if (course && moduleId && topicId) {
      const module = course.modules.find(m => m.id === moduleId);
      const topic = module?.topics.find(t => t.id === topicId);
      
      if (topic) {
        setCurrentTopic(topic);
        // Use note content if available, fallback to topic content (migration support), or empty
        const initialContent = topic.note?.content || topic.content || '';
        setContent(initialContent);
        setCurrentNote(topic.note || null);
      } else {
        setError('Topic not found');
      }
    }
  }, [course, moduleId, topicId]);

  // Adjust textarea height on content change
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [content]);

  const fetchCourse = async (id: string) => {
    try {
      setIsLoading(true);
      const data = await coursesApi.getCourse(id);
      setCourse(data);
    } catch (err) {
      setError('Failed to load course');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newContent = e.target.value;
    setContent(newContent);
    setIsSaved(false); // Reset saved state immediately on change
    
    // Clear existing timeout
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }

    // Debounce save (auto-save after 1s of inactivity)
    saveTimeoutRef.current = setTimeout(async () => {
      if (courseId && moduleId && topicId && currentTopic) {
        setIsSaving(true);
        try {
          if (currentNote) {
            // Update existing note
            const updatedNote = await notesApi.updateNote(currentNote.id, { 
              content: newContent,
              title: currentTopic.title // Keep title in sync if needed
            });
            setCurrentNote(updatedNote);
          } else {
            // Create new note
            const newNote = await notesApi.createNote({
              title: currentTopic.title,
              content: newContent,
              courseId: courseId,
              topicId: topicId,
            });
            setCurrentNote(newNote);
          }
          
          setIsSaving(false);
          setIsSaved(true);
          
          // Hide saved indicator after 2 seconds
          if (savedIndicatorTimeoutRef.current) {
            clearTimeout(savedIndicatorTimeoutRef.current);
          }
          savedIndicatorTimeoutRef.current = setTimeout(() => {
            setIsSaved(false);
          }, 2000);
          
        } catch (err) {
          console.error('Failed to save content', err);
          setIsSaving(false);
        }
      }
    }, 1000);
  };

  const handleStudy = () => {
    // Placeholder for AI study feature
    console.log('Study feature triggered');
  };

  const handleResources = () => {
    // Placeholder for resources
    console.log('Resources clicked');
  };

  const handleToggleComplete = async () => {
    if (!currentTopic || !courseId || !moduleId || isCompleting) return;

    try {
      setIsCompleting(true);
      const newStatus = !currentTopic.completed;
      await coursesApi.toggleTopicCompletion(courseId, moduleId, currentTopic.id, newStatus);
      
      // Optimistic update
      setCurrentTopic(prev => prev ? ({ ...prev, completed: newStatus }) : null);
      
      // Update course state locally to reflect progress
      setCourse(prev => {
        if (!prev) return null;
        const updatedModules = prev.modules.map(m => {
          if (m.id === moduleId) {
            return {
              ...m,
              topics: m.topics.map(t => t.id === currentTopic.id ? { ...t, completed: newStatus } : t)
            };
          }
          return m;
        });
        return { ...prev, modules: updatedModules };
      });

    } catch (err) {
      console.error('Failed to update topic status', err);
    } finally {
      setIsCompleting(false);
    }
  };

  const findNextTopic = () => {
    if (!course || !currentTopic) return null;
    
    // Flatten all topics
    const allTopics = course.modules.flatMap(m => m.topics.map(t => ({ ...t, moduleId: m.id })));
    const currentIndex = allTopics.findIndex(t => t.id === currentTopic.id);
    
    if (currentIndex < allTopics.length - 1) {
      return allTopics[currentIndex + 1];
    }
    return null;
  };

  const findPrevTopic = () => {
    if (!course || !currentTopic) return null;
    
    const allTopics = course.modules.flatMap(m => m.topics.map(t => ({ ...t, moduleId: m.id })));
    const currentIndex = allTopics.findIndex(t => t.id === currentTopic.id);
    
    if (currentIndex > 0) {
      return allTopics[currentIndex - 1];
    }
    return null;
  };

  const nextTopic = findNextTopic();
  const prevTopic = findPrevTopic();

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  if (error || !currentTopic) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500 mb-4">{error || 'Topic not found'}</p>
        <Link to={`/courses/${courseId}`} className="text-indigo-600 hover:text-indigo-800">
          Back to Course
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="mb-6">
        <Link to={`/courses/${courseId}`} className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1 mb-4">
          <ArrowLeft className="w-4 h-4" />
          Back to Course
        </Link>
        
        <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4">
          <h1 className="text-2xl font-bold text-gray-900">{currentTopic.title}</h1>
          
          <div className="flex items-center gap-3">
            <div className="h-6 flex items-center mr-2">
              {isSaving ? (
                <div className="text-sm text-gray-400 flex items-center gap-2 animate-pulse">
                  <Save className="w-4 h-4" />
                  <span className="hidden sm:inline">Saving...</span>
                </div>
              ) : isSaved ? (
                <div className="text-sm text-green-600 flex items-center gap-2 animate-in fade-in duration-300">
                  <Check className="w-4 h-4" />
                  <span className="hidden sm:inline">Saved</span>
                </div>
              ) : null}
            </div>

            <button
              onClick={handleResources}
              className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
            >
              <FileText className="w-4 h-4" />
              Resources
            </button>

            <button
              onClick={handleStudy}
              className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-white bg-purple-600 rounded-lg hover:bg-purple-700 transition-colors shadow-sm"
            >
              <Brain className="w-4 h-4" />
              Study
            </button>
          </div>
        </div>
      </div>

      {/* Content Editor */}
      <div className="flex-1 bg-white p-4 sm:p-8 rounded-xl border border-gray-200 shadow-sm mb-8 relative">
        <textarea
          ref={textareaRef}
          value={content}
          onChange={handleContentChange}
          placeholder="Start typing your notes here..."
          className="w-full h-full min-h-[300px] resize-none border-none outline-none text-base sm:text-lg text-gray-800 leading-relaxed font-sans bg-transparent placeholder-gray-300"
          spellCheck={false}
        />
        {/* Helper text if empty */}
        {content.length === 0 && (
          <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 pointer-events-none text-center opacity-0">
             {/* This is just a placeholder for structure, the real placeholder is on the textarea */}
          </div>
        )}
      </div>

      {/* Footer Navigation */}
      <div className="border-t border-gray-200 pt-6 flex flex-col-reverse sm:flex-row justify-between items-center gap-4 sm:gap-0">
        <div className="w-full sm:w-1/3 flex justify-start">
          {prevTopic && (
            <Link 
              to={`/courses/${courseId}/modules/${prevTopic.moduleId}/topics/${prevTopic.id}`}
              className="flex items-center gap-2 text-gray-600 hover:text-indigo-600 font-medium truncate max-w-full"
            >
              <ChevronLeft className="w-4 h-4 flex-shrink-0" />
              <span className="truncate">{prevTopic.title}</span>
            </Link>
          )}
        </div>

        <div className="flex justify-center w-full sm:w-1/3">
          <button 
            onClick={handleToggleComplete}
            disabled={isCompleting}
            className={cn(
              "w-full sm:w-auto flex items-center justify-center gap-2 px-6 py-3 rounded-lg font-medium transition-all",
              currentTopic.completed 
                ? "bg-green-100 text-green-700 hover:bg-green-200" 
                : "bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm hover:shadow"
            )}
          >
            {isCompleting ? (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current"></div>
            ) : currentTopic.completed ? (
              <CheckCircle className="w-5 h-5" />
            ) : (
              <Circle className="w-5 h-5" />
            )}
            <span>{currentTopic.completed ? 'Completed' : 'Mark as Complete'}</span>
          </button>
        </div>

        <div className="w-full sm:w-1/3 flex justify-end">
          {nextTopic && (
            <Link 
              to={`/courses/${courseId}/modules/${nextTopic.moduleId}/topics/${nextTopic.id}`}
              className="flex items-center gap-2 text-gray-600 hover:text-indigo-600 font-medium truncate max-w-full"
            >
              <span className="truncate">{nextTopic.title}</span>
              <ChevronRight className="w-4 h-4 flex-shrink-0" />
            </Link>
          )}
        </div>
      </div>
    </div>
  );
};
