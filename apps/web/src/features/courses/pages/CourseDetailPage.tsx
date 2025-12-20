import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import type { Course, UpdateCourseRequest, Difficulty, Module, Topic } from '../types/courses.types';
import { 
  ArrowLeft, CheckCircle, Circle, PlayCircle, BookOpen, Trash2, Edit2, 
  MoreVertical, Save, X, Plus, GripVertical, ChevronDown, ChevronUp 
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DeleteConfirmationModal } from '../components/modals/DeleteConfirmationModal';
import { Reorder, useDragControls } from 'framer-motion';

// Temporary local types for editing state
interface EditingModule extends Module {
  isNew?: boolean;
}

interface EditingTopic extends Topic {
  isNew?: boolean;
}

// Wrapper component for sortable topic item
const SortableTopicItem = ({ 
  topic, 
  moduleId, 
  isEditing, 
  courseId,
  onUpdateTitle, 
  onDelete 
}: { 
  topic: Topic, 
  moduleId: string, 
  isEditing: boolean, 
  courseId: string,
  onUpdateTitle: (moduleId: string, topicId: string, newTitle: string) => void,
  onDelete: (type: 'topic', id: string, moduleId: string) => void
}) => {
  const controls = useDragControls();

  if (isEditing) {
    return (
      <Reorder.Item 
        value={topic} 
        id={topic.id}
        dragListener={false}
        dragControls={controls}
        className="relative group bg-white border-b border-gray-100 last:border-0"
      >
        <div className="flex items-center gap-3 sm:gap-4 px-4 py-3 sm:px-6 sm:py-4">
          <div 
            onPointerDown={(e) => controls.start(e)}
            className="cursor-move touch-none p-1 -ml-1 hover:bg-gray-100 rounded"
          >
            <GripVertical className="w-4 h-4 text-gray-400" />
          </div>
          <div className="flex-1">
            <input
              type="text"
              value={topic.title}
              onChange={(e) => onUpdateTitle(moduleId, topic.id, e.target.value)}
              className="w-full bg-transparent border-b border-transparent hover:border-gray-300 focus:border-indigo-500 outline-none py-1 text-sm font-medium text-gray-900"
              placeholder="Topic Title"
            />
          </div>
          <button
            onClick={() => onDelete('topic', topic.id, moduleId)}
            className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </Reorder.Item>
    );
  }

  return (
    <div className="border-b border-gray-100 last:border-0">
      <Link 
        to={`/courses/${courseId}/modules/${moduleId}/topics/${topic.id}`}
        className="flex items-center gap-3 sm:gap-4 px-4 py-3 sm:px-6 sm:py-4 hover:bg-gray-50 transition-colors"
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
    </div>
  );
};

export const CourseDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [course, setCourse] = useState<Course | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Edit Mode State
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<{
    title: string;
    description: string;
    difficulty: Difficulty;
  } | null>(null);
  
  // Modals
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    type: 'course' | 'module' | 'topic';
    id: string;
    moduleId?: string; // for topic deletion
  } | null>(null);

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

  const handleStartEdit = () => {
    if (!course) return;
    setEditForm({
      title: course.title,
      description: course.description || '',
      difficulty: course.difficulty,
    });
    setIsEditing(true);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditForm(null);
    if (id) fetchCourse(id);
  };

  const handleSaveCourseDetails = async () => {
    if (!course || !editForm) return;
    try {
      const updated = await coursesApi.updateCourse(course.id, {
        title: editForm.title,
        description: editForm.description,
        difficulty: editForm.difficulty,
      });
      setCourse(prev => prev ? ({ ...prev, ...updated }) : null);
      setIsEditing(false);
    } catch (err) {
      console.error('Failed to update course', err);
    }
  };

  const handleDeleteClick = (type: 'course' | 'module' | 'topic', id: string, moduleId?: string) => {
    setDeleteTarget({ type, id, moduleId });
    setIsDeleteModalOpen(true);
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget || !course) return;
    
    setIsDeleting(true);
    try {
      if (deleteTarget.type === 'course') {
        await coursesApi.deleteCourse(deleteTarget.id);
        navigate('/courses');
      } else if (deleteTarget.type === 'module') {
        await coursesApi.deleteModule(course.id, deleteTarget.id);
        setCourse(prev => prev ? ({
          ...prev,
          modules: prev.modules.filter(m => m.id !== deleteTarget.id)
        }) : null);
        setIsDeleteModalOpen(false);
      } else if (deleteTarget.type === 'topic' && deleteTarget.moduleId) {
        await coursesApi.deleteTopic(course.id, deleteTarget.moduleId, deleteTarget.id);
        setCourse(prev => {
          if (!prev) return null;
          return {
            ...prev,
            modules: prev.modules.map(m => {
              if (m.id === deleteTarget.moduleId) {
                return {
                  ...m,
                  topics: m.topics.filter(t => t.id !== deleteTarget.id)
                };
              }
              return m;
            })
          };
        });
        setIsDeleteModalOpen(false);
      }
    } catch (err) {
      console.error('Failed to delete item', err);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleAddModule = async () => {
    if (!course) return;
    const newOrder = course.modules.length > 0 
      ? Math.max(...course.modules.map(m => m.order)) + 1 
      : 0;
      
    try {
      const newModule = await coursesApi.createModule(course.id, {
        title: 'New Module',
        order: newOrder,
        description: '',
      });
      setCourse(prev => prev ? ({
        ...prev,
        modules: [...prev.modules, newModule]
      }) : null);
    } catch (err) {
      console.error('Failed to create module', err);
    }
  };

  const handleAddTopic = async (moduleId: string) => {
    if (!course) return;
    const module = course.modules.find(m => m.id === moduleId);
    if (!module) return;

    const newOrder = module.topics.length > 0
      ? Math.max(...module.topics.map(t => t.order)) + 1
      : 0;

    try {
      const newTopic = await coursesApi.createTopic(course.id, moduleId, {
        title: 'New Topic',
        order: newOrder,
        content: '',
      });
      
      setCourse(prev => {
        if (!prev) return null;
        return {
          ...prev,
          modules: prev.modules.map(m => {
            if (m.id === moduleId) {
              return {
                ...m,
                topics: [...m.topics, newTopic]
              };
            }
            return m;
          })
        };
      });
    } catch (err) {
      console.error('Failed to create topic', err);
    }
  };

  const handleModuleMove = async (moduleId: string, direction: 'up' | 'down') => {
    if (!course) return;
    const modules = [...course.modules];
    const index = modules.findIndex(m => m.id === moduleId);
    if (index === -1) return;
    
    if (direction === 'up' && index > 0) {
      const current = modules[index];
      const prev = modules[index - 1];
      
      const tempOrder = current.order;
      current.order = prev.order;
      prev.order = tempOrder;
      
      modules[index] = prev;
      modules[index - 1] = current;
      setCourse(prev => prev ? ({ ...prev, modules }) : null);

      await Promise.all([
        coursesApi.updateModule(course.id, current.id, { order: current.order }),
        coursesApi.updateModule(course.id, prev.id, { order: prev.order })
      ]);
    } else if (direction === 'down' && index < modules.length - 1) {
      const current = modules[index];
      const next = modules[index + 1];
      
      const tempOrder = current.order;
      current.order = next.order;
      next.order = tempOrder;

      modules[index] = next;
      modules[index + 1] = current;
      setCourse(prev => prev ? ({ ...prev, modules }) : null);
      
      await Promise.all([
        coursesApi.updateModule(course.id, current.id, { order: current.order }),
        coursesApi.updateModule(course.id, next.id, { order: next.order })
      ]);
    }
  };

  // Topic reordering handler
  const handleTopicReorder = async (moduleId: string, newTopics: Topic[]) => {
    if (!course) return;

    // Optimistic update
    setCourse(prev => {
      if (!prev) return null;
      return {
        ...prev,
        modules: prev.modules.map(m => {
          if (m.id === moduleId) {
            return { ...m, topics: newTopics };
          }
          return m;
        })
      };
    });

    // We only want to trigger API calls when drag ends to avoid spamming
    // But framer-motion's Reorder component updates state continuously.
    // So we rely on the parent component logic or a dedicated "save order" function.
    // For now, let's just update the local state here. 
    // The actual API call needs to happen on drag end, but Reorder.Group doesn't give us onDragEnd easily for the whole list.
    // The SortableTopicItem has onDragEnd but it doesn't know the new order of the list.
    // 
    // Strategy: We update local state here.
    // And we debounce or check for changes separately? 
    // Or we just update the order index of all items in the list and save them?
    
    // Let's loop and update orders for all topics in this module to match their new index
    // This is "heavy" but ensures consistency.
    // Since we can't easily debounce this in this simple setup without refs, 
    // we'll update the orders in the backend for *all* topics in the module whenever the list changes.
    // To prevent spam, usually one would use onDragEnd.
    // Let's implement onDragEnd in SortableTopicItem to call a "saveOrder" function passed down.
  };
  
  const handleSaveTopicOrder = async (moduleId: string) => {
    // This function is called when drag ends
    if (!course) return;
    const module = course.modules.find(m => m.id === moduleId);
    if (!module) return;

    // Update order of all topics based on their current array index
    const updates = module.topics.map((topic, index) => {
      if (topic.order !== index) {
        return coursesApi.updateTopic(course.id, moduleId, topic.id, { order: index });
      }
      return Promise.resolve();
    });

    try {
      await Promise.all(updates);
    } catch (err) {
      console.error('Failed to save topic order', err);
    }
  };

  const handleUpdateModuleTitle = async (moduleId: string, newTitle: string) => {
    if (!course) return;
    setCourse(prev => {
      if (!prev) return null;
      return {
        ...prev,
        modules: prev.modules.map(m => m.id === moduleId ? { ...m, title: newTitle } : m)
      };
    });
    await coursesApi.updateModule(course.id, moduleId, { title: newTitle });
  };
  
  const handleUpdateModuleDescription = async (moduleId: string, newDescription: string) => {
    if (!course) return;
    setCourse(prev => {
        if (!prev) return null;
        return {
            ...prev,
            modules: prev.modules.map(m => m.id === moduleId ? { ...m, description: newDescription } : m)
        };
    });
    // Debounce this in a real app, but for now direct update is acceptable or onBlur
  };
  
  const handleSaveModuleDescription = async (moduleId: string, description: string) => {
      if (!course) return;
      await coursesApi.updateModule(course.id, moduleId, { description });
  };

  const handleUpdateTopicTitle = async (moduleId: string, topicId: string, newTitle: string) => {
    if (!course) return;
    setCourse(prev => {
      if (!prev) return null;
      return {
        ...prev,
        modules: prev.modules.map(m => {
          if (m.id === moduleId) {
            return {
              ...m,
              topics: m.topics.map(t => t.id === topicId ? { ...t, title: newTitle } : t)
            };
          }
          return m;
        })
      };
    });
    await coursesApi.updateTopic(course.id, moduleId, topicId, { title: newTitle });
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
    <div className="max-w-4xl mx-auto space-y-8 pb-20">
      {/* Header & Controls */}
      <div>
        <div className="flex justify-between items-start mb-4">
          <Link to="/courses" className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" />
            Back to Courses
          </Link>
          
          <div className="flex items-center gap-2">
            {isEditing ? (
              <>
                <button
                  onClick={handleCancelEdit}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 flex items-center gap-2"
                >
                  <X className="w-4 h-4" />
                  Done
                </button>
                <button
                  onClick={handleSaveCourseDetails}
                  className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 flex items-center gap-2"
                >
                  <Save className="w-4 h-4" />
                  Save
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={handleStartEdit}
                  className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 flex items-center gap-2"
                >
                  <Edit2 className="w-4 h-4" />
                  Edit
                </button>
                <button
                  onClick={() => handleDeleteClick('course', course.id)}
                  className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                  title="Delete Course"
                >
                  <Trash2 className="w-5 h-5" />
                </button>
              </>
            )}
          </div>
        </div>

        {/* Course Details Form / Display */}
        {isEditing && editForm ? (
          <div className="bg-white p-6 rounded-xl border border-indigo-100 shadow-sm space-y-4 mb-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Course Title</label>
              <input
                type="text"
                value={editForm.title}
                onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <textarea
                rows={3}
                value={editForm.description}
                onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Difficulty</label>
              <select
                value={editForm.difficulty}
                onChange={(e) => setEditForm({ ...editForm, difficulty: e.target.value as Difficulty })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
              >
                <option value="BEGINNER">Beginner</option>
                <option value="INTERMEDIATE">Intermediate</option>
                <option value="ADVANCED">Advanced</option>
                <option value="EXPERT">Expert</option>
              </select>
            </div>
          </div>
        ) : (
          <>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">{course.title}</h1>
            <p className="text-gray-600 text-lg">{course.description}</p>
            
            <div className="mt-6 flex flex-wrap items-center gap-4 sm:gap-6 text-sm text-gray-500">
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
          </>
        )}
      </div>

      {/* Progress (Only show in view mode) */}
      {!isEditing && (
        <div className="bg-white p-4 sm:p-6 rounded-xl border border-gray-100 shadow-sm">
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
      )}

      {/* Modules List */}
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <h2 className="text-xl font-bold text-gray-900">Course Content</h2>
          {isEditing && (
            <button
              onClick={handleAddModule}
              className="px-3 py-1.5 text-sm font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg flex items-center gap-1 transition-colors"
            >
              <Plus className="w-4 h-4" />
              Add Module
            </button>
          )}
        </div>
        
        <div className="space-y-4">
          {course.modules.length === 0 && (
            <div className="text-center py-8 border-2 border-dashed border-gray-200 rounded-xl">
              <p className="text-gray-500">No modules yet.</p>
            </div>
          )}

          {course.modules.map((module, mIndex) => (
            <div key={module.id} className={cn(
              "bg-white rounded-xl border overflow-hidden transition-all",
              isEditing ? "border-indigo-100 ring-1 ring-indigo-50" : "border-gray-200"
            )}>
              {/* Module Header */}
              <div className="bg-gray-50 px-4 py-3 sm:px-6 sm:py-4 border-b border-gray-200 flex items-center gap-4">
                {isEditing && (
                  <div className="flex flex-col gap-1 text-gray-400">
                    <button 
                      onClick={() => handleModuleMove(module.id, 'up')}
                      disabled={mIndex === 0}
                      className="hover:text-indigo-600 disabled:opacity-30"
                    >
                      <ChevronUp className="w-4 h-4" />
                    </button>
                    <button 
                      onClick={() => handleModuleMove(module.id, 'down')}
                      disabled={mIndex === course.modules.length - 1}
                      className="hover:text-indigo-600 disabled:opacity-30"
                    >
                      <ChevronDown className="w-4 h-4" />
                    </button>
                  </div>
                )}
                
                <div className="flex-1">
                  {isEditing ? (
                    <div className="space-y-2">
                        <input
                        type="text"
                        value={module.title}
                        onChange={(e) => handleUpdateModuleTitle(module.id, e.target.value)}
                        className="w-full bg-white px-2 py-1 border border-gray-300 rounded focus:ring-2 focus:ring-indigo-500 outline-none font-semibold text-gray-900"
                        placeholder="Module Title"
                        />
                        <textarea
                            rows={2}
                            value={module.description || ''}
                            onChange={(e) => handleUpdateModuleDescription(module.id, e.target.value)}
                            onBlur={(e) => handleSaveModuleDescription(module.id, e.target.value)}
                            className="w-full bg-white px-2 py-1 border border-gray-300 rounded focus:ring-2 focus:ring-indigo-500 outline-none text-sm text-gray-600"
                            placeholder="Module Description"
                        />
                    </div>
                  ) : (
                    <>
                        <h3 className="font-semibold text-gray-900">{module.title}</h3>
                        {module.description && <p className="text-sm text-gray-500 mt-1">{module.description}</p>}
                    </>
                  )}
                </div>

                {isEditing && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleDeleteClick('module', module.id)}
                      className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded"
                      title="Delete Module"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
              
              <div className="divide-y divide-gray-100">
                {module.topics.length === 0 && isEditing && (
                   <div className="p-4 text-center text-sm text-gray-400 italic">
                     No topics. Click + to add one.
                   </div>
                )}

                {isEditing ? (
                  <Reorder.Group 
                    axis="y" 
                    values={module.topics} 
                    onReorder={(newTopics) => handleTopicReorder(module.id, newTopics)}
                  >
                    {module.topics.map((topic) => (
                      <SortableTopicItem 
                        key={topic.id} 
                        topic={topic} 
                        moduleId={module.id} 
                        isEditing={true}
                        courseId={course.id}
                        onUpdateTitle={handleUpdateTopicTitle}
                        onDelete={handleDeleteClick}
                      />
                    ))}
                  </Reorder.Group>
                ) : (
                  module.topics.map((topic) => (
                    <SortableTopicItem 
                        key={topic.id} 
                        topic={topic} 
                        moduleId={module.id} 
                        isEditing={false}
                        courseId={course.id}
                        onUpdateTitle={handleUpdateTopicTitle}
                        onDelete={handleDeleteClick}
                      />
                  ))
                )}
                
                {isEditing && (
                    <div 
                        onPointerUp={() => handleSaveTopicOrder(module.id)} // Hack to trigger save on drop since drag ends on pointer up
                        className="p-2"
                    >
                         <button
                            onClick={() => handleAddTopic(module.id)}
                            className="w-full py-2 flex items-center justify-center gap-2 text-sm font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg border border-dashed border-indigo-200 transition-colors"
                        >
                            <Plus className="w-4 h-4" />
                            Add Topic
                        </button>
                    </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Delete Modal */}
      <DeleteConfirmationModal
        isOpen={isDeleteModalOpen}
        onClose={() => setIsDeleteModalOpen(false)}
        onConfirm={handleConfirmDelete}
        title={`Delete ${deleteTarget?.type === 'course' ? 'Course' : deleteTarget?.type === 'module' ? 'Module' : 'Topic'}`}
        message="Are you sure you want to delete this item? This action cannot be undone."
        isDeleting={isDeleting}
      />
    </div>
  );
};
