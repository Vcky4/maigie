import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import type { Course, Topic } from '../types/courses.types';
import { ArrowLeft, CheckCircle, Circle, ChevronRight, ChevronLeft } from 'lucide-react';
import { cn } from '../../../lib/utils';

export const TopicPage = () => {
  const { courseId, moduleId, topicId } = useParams<{ courseId: string; moduleId: string; topicId: string }>();
  
  const [course, setCourse] = useState<Course | null>(null);
  const [currentTopic, setCurrentTopic] = useState<Topic | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isCompleting, setIsCompleting] = useState(false);

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
      } else {
        setError('Topic not found');
      }
    }
  }, [course, moduleId, topicId]);

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
        <h1 className="text-2xl font-bold text-gray-900">{currentTopic.title}</h1>
      </div>

      {/* Content */}
      <div className="flex-1 bg-white p-8 rounded-xl border border-gray-200 shadow-sm mb-8">
        {currentTopic.content ? (
          <div className="prose max-w-none prose-indigo prose-img:rounded-xl prose-headings:font-bold prose-a:text-indigo-600">
             {/* Note: In a real app we'd use a markdown renderer here */}
            <div className="whitespace-pre-wrap font-sans text-gray-800 leading-relaxed">
              {currentTopic.content}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <BookOpen className="w-12 h-12 mb-4 opacity-50" />
            <p>No content available for this topic yet.</p>
          </div>
        )}
      </div>

      {/* Footer Navigation */}
      <div className="border-t border-gray-200 pt-6 flex justify-between items-center">
        <div className="w-1/3">
          {prevTopic && (
            <Link 
              to={`/courses/${courseId}/modules/${prevTopic.moduleId}/topics/${prevTopic.id}`}
              className="flex items-center gap-2 text-gray-600 hover:text-indigo-600 font-medium truncate"
            >
              <ChevronLeft className="w-4 h-4 flex-shrink-0" />
              <span className="truncate">{prevTopic.title}</span>
            </Link>
          )}
        </div>

        <div className="flex justify-center w-1/3">
          <button 
            onClick={handleToggleComplete}
            disabled={isCompleting}
            className={cn(
              "flex items-center gap-2 px-6 py-3 rounded-lg font-medium transition-all",
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

        <div className="w-1/3 flex justify-end">
          {nextTopic && (
            <Link 
              to={`/courses/${courseId}/modules/${nextTopic.moduleId}/topics/${nextTopic.id}`}
              className="flex items-center gap-2 text-gray-600 hover:text-indigo-600 font-medium truncate"
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

