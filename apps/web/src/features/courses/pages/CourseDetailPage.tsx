import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import type { Course, UpdateCourseRequest } from '../types/courses.types';
import { ArrowLeft, CheckCircle, Circle, PlayCircle, BookOpen, Trash2, Edit2, MoreVertical } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DeleteConfirmationModal } from '../components/modals/DeleteConfirmationModal';
import { EditCourseModal } from '../components/modals/EditCourseModal';

export const CourseDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [course, setCourse] = useState<Course | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    if (id) {
      fetchCourse(id);
    }
  }, [id]);

  const fetchCourse = async (courseId: string) => {
    try {
      setIsLoading(true);
      const data = await coursesApi.getCourse(courseId);
      setCourse(data);
    } catch (err) {
      setError('Failed to load course details');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteCourse = async () => {
    if (!course) return;
    setIsDeleting(true);
    try {
      await coursesApi.deleteCourse(course.id);
      navigate('/courses');
    } catch (err) {
      console.error('Failed to delete course', err);
      // Ideally show a toast notification here
    } finally {
      setIsDeleting(false);
      setIsDeleteModalOpen(false);
    }
  };

  const handleUpdateCourse = async (data: UpdateCourseRequest) => {
    if (!course) return;
    const updatedCourse = await coursesApi.updateCourse(course.id, data);
    setCourse(updatedCourse);
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  if (error || !course) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500 mb-4">{error || 'Course not found'}</p>
        <Link to="/courses" className="text-indigo-600 hover:text-indigo-800 font-medium flex items-center justify-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          Back to Courses
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <div className="flex justify-between items-start mb-4">
          <Link to="/courses" className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" />
            Back to Courses
          </Link>
          
          <div className="flex items-center gap-2">
            <button
              onClick={() => setIsEditModalOpen(true)}
              className="p-2 text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
              title="Edit Course"
            >
              <Edit2 className="w-5 h-5" />
            </button>
            <button
              onClick={() => setIsDeleteModalOpen(true)}
              className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              title="Delete Course"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </div>
        </div>

        <h1 className="text-3xl font-bold text-gray-900 mb-2">{course.title}</h1>
        <p className="text-gray-600 text-lg">{course.description}</p>
        
        <div className="mt-6 flex items-center gap-6 text-sm text-gray-500">
          <div className="flex items-center gap-2">
            <span className={cn(
              "px-2.5 py-0.5 rounded-full text-xs font-medium",
              course.difficulty === 'BEGINNER' ? "bg-green-100 text-green-800" :
              course.difficulty === 'INTERMEDIATE' ? "bg-blue-100 text-blue-800" :
              "bg-purple-100 text-purple-800"
            )}>
              {course.difficulty}
            </span>
          </div>
          <span>{course.modules.length} Modules</span>
          <span>{course.totalTopics} Topics</span>
        </div>
      </div>

      {/* Progress */}
      <div className="bg-white p-6 rounded-xl border border-gray-100 shadow-sm">
        <div className="flex justify-between items-end mb-2">
          <div>
            <span className="text-2xl font-bold text-gray-900">{Math.round(course.progress)}%</span>
            <span className="text-gray-500 ml-2">Complete</span>
          </div>
          <div className="text-sm text-gray-500">
            {course.completedTopics} / {course.totalTopics} topics
          </div>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-3">
          <div 
            className="bg-indigo-600 h-3 rounded-full transition-all duration-500" 
            style={{ width: `${course.progress}%` }}
          />
        </div>
      </div>

      {/* Modules List */}
      <div className="space-y-6">
        <h2 className="text-xl font-bold text-gray-900">Course Content</h2>
        
        <div className="space-y-4">
          {course.modules.map((module) => (
            <div key={module.id} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <div className="bg-gray-50 px-6 py-4 border-b border-gray-200">
                <h3 className="font-semibold text-gray-900">{module.title}</h3>
                {module.description && <p className="text-sm text-gray-500 mt-1">{module.description}</p>}
              </div>
              
              <div className="divide-y divide-gray-100">
                {module.topics.map((topic) => (
                  <Link 
                    key={topic.id}
                    to={`/courses/${course.id}/modules/${module.id}/topics/${topic.id}`}
                    className="flex items-center gap-4 px-6 py-4 hover:bg-gray-50 transition-colors group"
                  >
                    <div className="flex-shrink-0">
                      {topic.completed ? (
                        <CheckCircle className="w-5 h-5 text-green-500" />
                      ) : (
                        <Circle className="w-5 h-5 text-gray-300 group-hover:text-indigo-500 transition-colors" />
                      )}
                    </div>
                    
                    <div className="flex-1">
                      <h4 className={cn(
                        "text-sm font-medium transition-colors",
                        topic.completed ? "text-gray-500 line-through" : "text-gray-900 group-hover:text-indigo-600"
                      )}>
                        {topic.title}
                      </h4>
                      {topic.estimatedHours && (
                        <span className="text-xs text-gray-400 mt-0.5 block">
                          {topic.estimatedHours} hours
                        </span>
                      )}
                    </div>

                    <div className="text-gray-300 group-hover:text-indigo-600">
                      {topic.completed ? (
                        <BookOpen className="w-5 h-5" />
                      ) : (
                        <PlayCircle className="w-5 h-5" />
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Modals */}
      <DeleteConfirmationModal
        isOpen={isDeleteModalOpen}
        onClose={() => setIsDeleteModalOpen(false)}
        onConfirm={handleDeleteCourse}
        title="Delete Course"
        message="Are you sure you want to delete this course? This action cannot be undone and all progress will be lost."
        isDeleting={isDeleting}
      />

      <EditCourseModal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        onSave={handleUpdateCourse}
        initialData={{
          title: course.title,
          description: course.description,
          difficulty: course.difficulty,
        }}
      />
    </div>
  );
};

