import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, Sparkles, BookOpen } from 'lucide-react';
import { coursesApi } from '../services/coursesApi';
import type { Difficulty } from '../types/courses.types';
import { cn } from '../../../lib/utils';

export const CourseCreatePage = () => {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'manual' | 'ai'>('manual');
  const [formData, setFormData] = useState({
    title: '',
    description: '',
    difficulty: 'BEGINNER' as Difficulty,
    targetDate: '',
  });
  const [topicPrompt, setTopicPrompt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError(null);

    try {
      if (mode === 'manual') {
        const course = await coursesApi.createCourse({
          ...formData,
          isAIGenerated: false,
        });
        navigate(`/courses/${course.id}`);
      } else {
        const response = await coursesApi.generateAICourse({
          topic: topicPrompt,
          difficulty: formData.difficulty,
        });
        navigate(`/courses/${response.courseId}`);
      }
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to create course. Please try again.');
      console.error(err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <Link to="/courses" className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1 mb-6">
        <ArrowLeft className="w-4 h-4" />
        Back to Courses
      </Link>
      
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="p-8 border-b border-gray-100">
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Create New Course</h1>
          <p className="text-gray-500">Choose how you want to start your learning journey.</p>
        </div>

        <div className="p-8">
          {/* Mode Selection */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
            <button
              type="button"
              onClick={() => setMode('manual')}
              className={cn(
                "flex flex-col items-center p-6 border-2 rounded-xl transition-all",
                mode === 'manual' 
                  ? "border-indigo-600 bg-indigo-50 text-indigo-900" 
                  : "border-gray-200 hover:border-indigo-200 hover:bg-gray-50 text-gray-600"
              )}
            >
              <BookOpen className={cn("w-8 h-8 mb-3", mode === 'manual' ? "text-indigo-600" : "text-gray-400")} />
              <span className="font-semibold">Manual Creation</span>
              <span className="text-sm text-center mt-1 opacity-80">Build your course structure from scratch</span>
            </button>

            <button
              type="button"
              onClick={() => setMode('ai')}
              className={cn(
                "flex flex-col items-center p-6 border-2 rounded-xl transition-all",
                mode === 'ai' 
                  ? "border-purple-600 bg-purple-50 text-purple-900" 
                  : "border-gray-200 hover:border-purple-200 hover:bg-gray-50 text-gray-600"
              )}
            >
              <Sparkles className={cn("w-8 h-8 mb-3", mode === 'ai' ? "text-purple-600" : "text-gray-400")} />
              <span className="font-semibold">AI Generated</span>
              <span className="text-sm text-center mt-1 opacity-80">Let AI design a curriculum for you</span>
            </button>
          </div>

          {mode === 'manual' ? (
            <form onSubmit={handleSubmit} className="space-y-6">
              <div>
                <label htmlFor="title" className="block text-sm font-medium text-gray-700 mb-1">
                  Course Title
                </label>
                <input
                  type="text"
                  id="title"
                  required
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                  placeholder="e.g. Advanced React Patterns"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
                />
              </div>

              <div>
                <label htmlFor="description" className="block text-sm font-medium text-gray-700 mb-1">
                  Description
                </label>
                <textarea
                  id="description"
                  rows={4}
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="What will you learn in this course?"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label htmlFor="difficulty" className="block text-sm font-medium text-gray-700 mb-1">
                    Difficulty Level
                  </label>
                  <select
                    id="difficulty"
                    value={formData.difficulty}
                    onChange={(e) => setFormData({ ...formData, difficulty: e.target.value as Difficulty })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
                  >
                    <option value="BEGINNER">Beginner</option>
                    <option value="INTERMEDIATE">Intermediate</option>
                    <option value="ADVANCED">Advanced</option>
                    <option value="EXPERT">Expert</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="targetDate" className="block text-sm font-medium text-gray-700 mb-1">
                    Target Completion Date (Optional)
                  </label>
                  <input
                    type="date"
                    id="targetDate"
                    value={formData.targetDate}
                    onChange={(e) => setFormData({ ...formData, targetDate: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
                  />
                </div>
              </div>

              {error && <p className="text-red-500 text-sm">{error}</p>}

              <div className="flex justify-end pt-4">
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-6 py-2.5 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 flex items-center gap-2"
                >
                  {isSubmitting ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Creating...
                    </>
                  ) : (
                    'Create Course'
                  )}
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-6">
              <div className="bg-purple-50 border border-purple-100 rounded-lg p-6 mb-6">
                <h3 className="font-semibold text-purple-900 mb-2">How it works</h3>
                <p className="text-purple-800 text-sm">
                  Describe what you want to learn, and our AI will generate a comprehensive curriculum for you, complete with modules and topics tailored to your difficulty level.
                </p>
              </div>

              <div>
                <label htmlFor="topicPrompt" className="block text-sm font-medium text-gray-700 mb-1">
                  What do you want to learn?
                </label>
                <input
                  type="text"
                  id="topicPrompt"
                  required
                  value={topicPrompt}
                  onChange={(e) => setTopicPrompt(e.target.value)}
                  placeholder="e.g. Python for Data Science, History of Ancient Rome, French Cooking Basics..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent outline-none"
                />
              </div>

              <div>
                <label htmlFor="aiDifficulty" className="block text-sm font-medium text-gray-700 mb-1">
                  Difficulty Level
                </label>
                <select
                  id="aiDifficulty"
                  value={formData.difficulty}
                  onChange={(e) => setFormData({ ...formData, difficulty: e.target.value as Difficulty })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent outline-none"
                >
                  <option value="BEGINNER">Beginner</option>
                  <option value="INTERMEDIATE">Intermediate</option>
                  <option value="ADVANCED">Advanced</option>
                  <option value="EXPERT">Expert</option>
                </select>
              </div>

              {error && <p className="text-red-500 text-sm">{error}</p>}

              <div className="flex justify-end pt-4">
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-6 py-2.5 bg-purple-600 text-white font-medium rounded-lg hover:bg-purple-700 transition-colors disabled:opacity-50 flex items-center gap-2"
                >
                  {isSubmitting ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Generating...
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4" />
                      Generate with AI
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};

