import React, { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import type { CourseListItem, Difficulty } from '../types/courses.types';
import { CourseCard } from '../components/CourseCard';
import { Plus, Search, Filter, X } from 'lucide-react';
import { cn } from '../../../lib/utils';

export const CourseListPage = () => {
  const [courses, setCourses] = useState<CourseListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Filters
  const [search, setSearch] = useState('');
  const [difficulty, setDifficulty] = useState<Difficulty | ''>('');
  const [isAIGenerated, setIsAIGenerated] = useState<boolean | undefined>(undefined);
  const [archived, setArchived] = useState<boolean | undefined>(false);
  const [showFilters, setShowFilters] = useState(false);

  const fetchCourses = useCallback(async () => {
    try {
      setIsLoading(true);
      const response = await coursesApi.listCourses({ 
        search: search || undefined,
        difficulty: difficulty || undefined,
        isAIGenerated: isAIGenerated,
        archived: archived,
      });
      setCourses(response.courses || []);
    } catch (err) {
      setError('Failed to load courses');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, [search, difficulty, isAIGenerated, archived]);

  useEffect(() => {
    fetchCourses();
  }, [fetchCourses]);

  // Listen for AI action events to refetch courses
  useEffect(() => {
    const handleActionEvent = (event: Event) => {
      const customEvent = event as CustomEvent;
      const { action, status } = customEvent.detail;

      // If a course was created, refetch the courses list
      if (action === 'create_course' && status === 'success') {
        fetchCourses();
      }
    };

    window.addEventListener('aiActionCompleted', handleActionEvent);
    return () => {
      window.removeEventListener('aiActionCompleted', handleActionEvent);
    };
  }, [fetchCourses]);


  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchCourses();
  };

  const clearFilters = () => {
    setSearch('');
    setDifficulty('');
    setIsAIGenerated(undefined);
    setArchived(false);
    // Note: fetchCourses will be triggered by useEffect dependencies, 
    // but search needs manual trigger or useEffect on search (which we avoided to prevent debounce complexity for now)
    // Let's trigger manual fetch after state updates if needed, or rely on effect.
    // Actually, since search isn't in dependency array, we need to trigger it.
    // But setting search to '' won't trigger effect. 
    // Let's just create a separate effect for search or call fetchCourses directly?
    // Calling fetchCourses directly here might use stale state.
    // Better to let user clear search manually or just add search to dependency with debounce in real app.
    // For now, we'll force a reload after timeout or just let user re-search.
    // Ideally:
    setTimeout(() => {
        coursesApi.listCourses({ archived: false }).then(res => setCourses(res.courses || []));
    }, 0);
  };

  return (
    <div className="space-y-6 px-4 sm:px-0">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <h1 className="text-2xl font-bold text-gray-900">My Courses</h1>
        <Link to="/courses/new" className="w-full sm:w-auto flex items-center justify-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">
          <Plus className="w-4 h-4" />
          <span>New Course</span>
        </Link>
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex gap-2">
            <form onSubmit={handleSearch} className="relative flex-1">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
                <input
                type="text"
                placeholder="Search courses..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all"
                />
            </form>
            <button 
                onClick={() => setShowFilters(!showFilters)}
                className={cn(
                    "px-4 py-2 border rounded-lg flex items-center gap-2 transition-colors",
                    showFilters ? "bg-indigo-50 border-indigo-200 text-indigo-700" : "bg-white border-gray-300 text-gray-700 hover:bg-gray-50"
                )}
            >
                <Filter className="w-4 h-4" />
                <span className="hidden sm:inline">Filters</span>
            </button>
        </div>

        {showFilters && (
            <div className="p-4 bg-white border border-gray-200 rounded-lg shadow-sm grid grid-cols-1 sm:grid-cols-3 gap-4 animate-in fade-in slide-in-from-top-2">
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Difficulty</label>
                    <select 
                        value={difficulty} 
                        onChange={(e) => setDifficulty(e.target.value as Difficulty | '')}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none text-sm"
                    >
                        <option value="">All Levels</option>
                        <option value="BEGINNER">Beginner</option>
                        <option value="INTERMEDIATE">Intermediate</option>
                        <option value="ADVANCED">Advanced</option>
                        <option value="EXPERT">Expert</option>
                    </select>
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Source</label>
                    <select 
                        value={isAIGenerated === undefined ? '' : isAIGenerated.toString()} 
                        onChange={(e) => {
                            const val = e.target.value;
                            setIsAIGenerated(val === '' ? undefined : val === 'true');
                        }}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none text-sm"
                    >
                        <option value="">All Courses</option>
                        <option value="true">AI Generated</option>
                        <option value="false">Manually Created</option>
                    </select>
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Status</label>
                    <div className="flex items-center gap-2 h-[38px]">
                        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
                            <input 
                                type="checkbox" 
                                checked={archived} 
                                onChange={(e) => setArchived(e.target.checked)}
                                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500" 
                            />
                            Show Archived
                        </label>
                    </div>
                </div>
                
                <div className="sm:col-span-3 flex justify-end">
                    <button 
                        onClick={clearFilters}
                        className="text-sm text-red-600 hover:text-red-700 flex items-center gap-1"
                    >
                        <X className="w-3 h-3" />
                        Clear Filters
                    </button>
                </div>
            </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
        </div>
      ) : error ? (
        <div className="text-center py-12 text-red-500">{error}</div>
      ) : courses.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-dashed border-gray-300">
          <div className="mx-auto h-12 w-12 text-gray-400 mb-4 flex justify-center items-center">
            <Plus className="w-6 h-6" />
          </div>
          <h3 className="text-lg font-medium text-gray-900">No courses found</h3>
          <p className="mt-1 text-sm text-gray-500">Try adjusting your filters or create a new course.</p>
          <button 
            onClick={clearFilters}
            className="mt-4 text-indigo-600 hover:text-indigo-800 font-medium text-sm"
          >
            Clear all filters
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {courses.map((course) => (
            <CourseCard key={course.id} course={course} />
          ))}
        </div>
      )}
    </div>
  );
};
