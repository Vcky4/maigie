import React, { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { notesApi } from '../services/notesApi';
import { coursesApi } from '../../courses/services/coursesApi';
import type { Note } from '../types/notes.types';
import type { CourseListItem, Course, Module, Topic } from '../../courses/types/courses.types';
import { ArrowLeft, Save, Check, Trash2, Calendar, Tag, X, Bold, Italic, List, Heading1, Heading2 } from 'lucide-react';
import { cn } from '../../../lib/utils';

export function NoteDetailPage() {
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const isNew = !noteId || noteId === 'new';

  const [note, setNote] = useState<Note | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState('');
  
  // Course Linking State
  const [courses, setCourses] = useState<CourseListItem[]>([]);
  const [selectedCourseId, setSelectedCourseId] = useState<string>('');
  const [selectedTopicId, setSelectedTopicId] = useState<string>('');
  const [courseStructure, setCourseStructure] = useState<Course | null>(null);
  const [loadingStructure, setLoadingStructure] = useState(false);

  const [isLoading, setIsLoading] = useState(!isNew);
  const [isSaving, setIsSaving] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const savedIndicatorTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    fetchCourses();
    if (!isNew && noteId) {
      fetchNote(noteId);
    }
  }, [noteId, isNew]);

  // Adjust textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [content]);

  // Fetch course structure when selectedCourseId changes
  useEffect(() => {
    if (selectedCourseId) {
      fetchCourseStructure(selectedCourseId);
    } else {
      setCourseStructure(null);
      setSelectedTopicId('');
    }
  }, [selectedCourseId]);

  const fetchCourses = async () => {
    try {
      const data = await coursesApi.listCourses({ pageSize: 100 });
      setCourses(data.courses);
    } catch (err) {
      console.error('Failed to load courses', err);
    }
  };

  const fetchCourseStructure = async (courseId: string) => {
    setLoadingStructure(true);
    try {
      const data = await coursesApi.getCourse(courseId);
      setCourseStructure(data);
    } catch (err) {
      console.error('Failed to load course structure', err);
    } finally {
      setLoadingStructure(false);
    }
  };

  const fetchNote = async (id: string) => {
    try {
      setIsLoading(true);
      const data = await notesApi.getNote(id);
      setNote(data);
      setTitle(data.title);
      setContent(data.content || '');
      setTags(data.tags?.map(t => t.tag) || []);
      
      if (data.courseId) {
        setSelectedCourseId(data.courseId);
        // Topic ID will be set, but we need structure first which useEffect handles
        if (data.topicId) {
            setSelectedTopicId(data.topicId);
        }
      }
    } catch (err) {
      setError('Failed to load note');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async (manual = false) => {
    if (!title.trim()) return;

    setIsSaving(true);
    try {
      const noteData = {
        title,
        content,
        tags,
        courseId: selectedCourseId || undefined,
        topicId: selectedTopicId || undefined,
      };

      if (isNew && !note) {
        // Create
        const newNote = await notesApi.createNote(noteData);
        setNote(newNote);
        // Replace URL without navigation to avoid reload
        window.history.replaceState(null, '', `/notes/${newNote.id}`);
      } else if (note) {
        // Update
        const updatedNote = await notesApi.updateNote(note.id, noteData);
        setNote(updatedNote);
      }
      
      setIsSaving(false);
      setIsSaved(true);
      
      if (savedIndicatorTimeoutRef.current) clearTimeout(savedIndicatorTimeoutRef.current);
      savedIndicatorTimeoutRef.current = setTimeout(() => setIsSaved(false), 2000);

      if (manual && isNew) {
        // Optional: redirect or notify
      }
    } catch (err) {
      console.error('Failed to save note', err);
      setIsSaving(false);
      if (manual) alert('Failed to save note');
    }
  };

  // Debounced auto-save for content changes
  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newContent = e.target.value;
    setContent(newContent);
    setIsSaved(false);

    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    
    if (!isNew || title.trim()) {
        saveTimeoutRef.current = setTimeout(() => {
            handleSave();
        }, 1500);
    }
  };

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTitle(e.target.value);
    setIsSaved(false);
    if (!isNew && saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    if (!isNew) {
        saveTimeoutRef.current = setTimeout(() => handleSave(), 1500);
    }
  };

  const handleDelete = async () => {
    if (!note || !window.confirm('Are you sure you want to delete this note?')) return;
    try {
      await notesApi.deleteNote(note.id);
      navigate('/notes');
    } catch (err) {
      console.error(err);
      alert('Failed to delete note');
    }
  };

  const handleAddTag = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && newTag.trim()) {
      e.preventDefault();
      if (!tags.includes(newTag.trim())) {
        const updatedTags = [...tags, newTag.trim()];
        setTags(updatedTags);
        setNewTag('');
        if (!isNew) {
             // In a real app, might want to save immediately or wait for auto-save
             // Triggering auto-save logic via effect or manual call if needed
             if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
             saveTimeoutRef.current = setTimeout(() => handleSave(), 1000);
        }
      }
    }
  };

  const removeTag = (tagToRemove: string) => {
    const updatedTags = tags.filter(t => t !== tagToRemove);
    setTags(updatedTags);
    if (!isNew) {
        if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = setTimeout(() => handleSave(), 1000);
    }
  };

  // Toolbar Actions
  const insertFormat = (prefix: string, suffix: string = '') => {
    if (!textareaRef.current) return;
    
    const start = textareaRef.current.selectionStart;
    const end = textareaRef.current.selectionEnd;
    const text = content;
    const before = text.substring(0, start);
    const selection = text.substring(start, end);
    const after = text.substring(end);

    const newContent = before + prefix + selection + suffix + after;
    setContent(newContent);
    
    // Restore selection / focus
    setTimeout(() => {
        if (textareaRef.current) {
            textareaRef.current.focus();
            textareaRef.current.setSelectionRange(start + prefix.length, end + prefix.length);
        }
    }, 0);

    // Trigger save
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    if (!isNew || title.trim()) {
        saveTimeoutRef.current = setTimeout(() => handleSave(), 1500);
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500 mb-4">{error}</p>
        <Link to="/notes" className="text-indigo-600 hover:text-indigo-800">
          Back to Notes
        </Link>
      </div>
    );
  }

  // Flatten topics for dropdown
  const availableTopics = courseStructure 
    ? courseStructure.modules.flatMap(m => m.topics.map(t => ({...t, moduleTitle: m.title}))) 
    : [];

  return (
    <div className="max-w-4xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="mb-6 space-y-4">
        <div className="flex items-center justify-between">
            <Link to="/notes" className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" />
            Back to Notes
            </Link>
            
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

                {isNew && (
                    <button
                    onClick={() => handleSave(true)}
                    disabled={isSaving || !title.trim()}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                    >
                    <Save className="w-4 h-4" />
                    Create Note
                    </button>
                )}

                {!isNew && (
                    <button
                    onClick={handleDelete}
                    className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    title="Delete Note"
                    >
                    <Trash2 className="w-5 h-5" />
                    </button>
                )}
            </div>
        </div>
        
        <input
            type="text"
            value={title}
            onChange={handleTitleChange}
            placeholder="Note Title"
            className="text-3xl font-bold text-gray-900 bg-transparent border-none focus:ring-0 placeholder-gray-300 w-full px-0"
        />
        
        {/* Metadata Controls */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-gray-50 p-4 rounded-lg border border-gray-100">
            <div className="space-y-3">
                {/* Course Selection */}
                <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Link to Course</label>
                    <select 
                        value={selectedCourseId}
                        onChange={(e) => setSelectedCourseId(e.target.value)}
                        className="block w-full text-sm border-gray-300 rounded-md shadow-sm focus:ring-indigo-500 focus:border-indigo-500 bg-white"
                    >
                        <option value="">Select a course...</option>
                        {courses.map(course => (
                            <option key={course.id} value={course.id}>{course.title}</option>
                        ))}
                    </select>
                </div>

                {/* Topic Selection */}
                <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Link to Topic</label>
                    <select 
                        value={selectedTopicId}
                        onChange={(e) => setSelectedTopicId(e.target.value)}
                        disabled={!selectedCourseId || loadingStructure}
                        className="block w-full text-sm border-gray-300 rounded-md shadow-sm focus:ring-indigo-500 focus:border-indigo-500 bg-white disabled:bg-gray-100 disabled:text-gray-400"
                    >
                        <option value="">Select a topic...</option>
                        {availableTopics.map(topic => (
                            <option key={topic.id} value={topic.id}>
                                {topic.moduleTitle} - {topic.title}
                            </option>
                        ))}
                    </select>
                </div>
            </div>

            <div className="space-y-3">
                {/* Tags */}
                <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Tags</label>
                    <div className="flex flex-wrap gap-2 items-center min-h-[38px] p-2 bg-white border border-gray-300 rounded-md shadow-sm">
                        {tags.map(tag => (
                            <span key={tag} className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-700">
                                {tag}
                                <button onClick={() => removeTag(tag)} className="ml-1 text-indigo-400 hover:text-indigo-900">
                                    <X className="w-3 h-3" />
                                </button>
                            </span>
                        ))}
                        <input
                            type="text"
                            value={newTag}
                            onChange={(e) => setNewTag(e.target.value)}
                            onKeyDown={handleAddTag}
                            placeholder={tags.length === 0 ? "Type tag & enter..." : ""}
                            className="bg-transparent border-none focus:ring-0 p-0 text-sm flex-1 min-w-[60px]"
                        />
                    </div>
                </div>
                
                {/* Timestamp */}
                {!isNew && note && (
                    <div className="flex items-center gap-2 text-xs text-gray-400 pt-2">
                        <Calendar className="w-3 h-3" />
                        <span>Last updated: {new Date(note.updatedAt).toLocaleString()}</span>
                    </div>
                )}
            </div>
        </div>
      </div>

      {/* Editor Toolbar */}
      <div className="flex items-center gap-1 p-2 bg-gray-50 border border-gray-200 border-b-0 rounded-t-xl overflow-x-auto">
        <button onClick={() => insertFormat('**', '**')} className="p-1.5 text-gray-600 hover:bg-gray-200 rounded" title="Bold">
            <Bold className="w-4 h-4" />
        </button>
        <button onClick={() => insertFormat('*', '*')} className="p-1.5 text-gray-600 hover:bg-gray-200 rounded" title="Italic">
            <Italic className="w-4 h-4" />
        </button>
        <div className="w-px h-4 bg-gray-300 mx-1" />
        <button onClick={() => insertFormat('# ', '')} className="p-1.5 text-gray-600 hover:bg-gray-200 rounded" title="Heading 1">
            <Heading1 className="w-4 h-4" />
        </button>
        <button onClick={() => insertFormat('## ', '')} className="p-1.5 text-gray-600 hover:bg-gray-200 rounded" title="Heading 2">
            <Heading2 className="w-4 h-4" />
        </button>
        <div className="w-px h-4 bg-gray-300 mx-1" />
        <button onClick={() => insertFormat('- ', '')} className="p-1.5 text-gray-600 hover:bg-gray-200 rounded" title="Bullet List">
            <List className="w-4 h-4" />
        </button>
      </div>

      {/* Editor */}
      <div className="flex-1 bg-white p-4 sm:p-8 rounded-b-xl border border-gray-200 shadow-sm mb-8 relative">
        <textarea
          ref={textareaRef}
          value={content}
          onChange={handleContentChange}
          placeholder="Start typing your notes here... (Markdown supported)"
          className="w-full h-full min-h-[400px] resize-none border-none outline-none text-base sm:text-lg text-gray-800 leading-relaxed font-sans bg-transparent placeholder-gray-300 font-mono"
          spellCheck={false}
        />
      </div>
    </div>
  );
}
