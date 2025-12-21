import React from 'react';
import { Link } from 'react-router-dom';
import type { CourseListItem } from '../types/courses.types';
import { cn } from '../../../lib/utils';

interface CourseCardProps {
  course: CourseListItem;
  className?: string;
}

export const CourseCard: React.FC<CourseCardProps> = ({ course, className }) => {
  return (
    <Link 
      to={`/courses/${course.id}`}
      className={cn(
        "block p-6 bg-white rounded-xl shadow-sm hover:shadow-md transition-all border border-gray-100",
        className
      )}
    >
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900 mb-1">{course.title}</h3>
          <p className="text-sm text-gray-500 line-clamp-2">{course.description || 'No description'}</p>
        </div>
        <span className={cn(
          "px-2 py-1 text-xs font-medium rounded-full",
          course.difficulty === 'BEGINNER' ? "bg-green-100 text-green-700" :
          course.difficulty === 'INTERMEDIATE' ? "bg-blue-100 text-blue-700" :
          course.difficulty === 'ADVANCED' ? "bg-purple-100 text-purple-700" :
          "bg-red-100 text-red-700"
        )}>
          {course.difficulty}
        </span>
      </div>
      
      <div className="mt-4">
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>Progress</span>
          <span>{Math.round(course.progress)}%</span>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-2">
          <div 
            className="bg-indigo-600 h-2 rounded-full transition-all duration-300" 
            style={{ width: `${course.progress}%` }}
          />
        </div>
      </div>
      
      <div className="mt-4 flex justify-between items-center text-xs text-gray-400">
        <span>{course.totalModules} Modules • {course.totalTopics} Topics</span>
        <span>{new Date(course.updatedAt).toLocaleDateString()}</span>
      </div>
    </Link>
  );
};

