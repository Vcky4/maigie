import React, { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { notesApi } from '../services/notesApi';
import { coursesApi } from '../../courses/services/coursesApi';
import type { Note, NoteAttachment } from '../types/notes.types';
import type { CourseListItem, Course, Module, Topic } from '../../courses/types/courses.types';
import { ArrowLeft, Save, Check, Trash2, Calendar, Tag, X, Bold, Italic, List, Heading1, Heading2, Paperclip, Loader, Eye } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { getFileIcon, getFileType } from '../../../lib/fileUtils';
import { FilePreviewModal } from '../../../components/common/FilePreviewModal';

// Helper for select arrow
const ChevronDownIcon = () => (
  <svg className="w-4 h-4 text-gray-500 pointer-events-none absolute right-3 top-1/2 -translate-y-1/2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
  </svg>
);

export function NoteDetailPage() {
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const isNew = !noteId || noteId === 'new';

  const [note, setNote] = useState<Note | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState('');
  const [attachments, setAttachments] = useState<NoteAttachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  
  // Preview Modal State
  const [previewFile, setPreviewFile] = useState<{ url: string; name: string } | null>(null);
  
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
      setAttachments(data.attachments || []);
      
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

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    
    // Auto-save the note if it's new before uploading attachment
    if (isNew && !note) {
        if (!title.trim()) {
            alert("Please enter a title before uploading attachments.");
            if (fileInputRef.current) fileInputRef.current.value = '';
            return;
        }
        
        // We need to create the note first
        setIsSaving(true);
        try {
             const noteData = {
                title,
                content,
                tags,
                courseId: selectedCourseId || undefined,
                topicId: selectedTopicId || undefined,
            };
            const newNote = await notesApi.createNote(noteData);
            setNote(newNote);
            window.history.replaceState(null, '', `/notes/${newNote.id}`);
            // Continue with upload using the new note ID
            await uploadAttachment(newNote.id, e.target.files[0]);
        } catch (err) {
            console.error('Failed to create note for upload', err);
            alert('Failed to save note before upload.');
        } finally {
            setIsSaving(false);
        }
    } else if (note) {
        await uploadAttachment(note.id, e.target.files[0]);
    }
  };

  const uploadAttachment = async (noteId: string, file: File) => {
    setIsUploading(true);
    try {
        // 1. Upload file to CDN
        const uploadResult = await notesApi.uploadFile(file);
        
        // 2. Link attachment to note
        const newAttachment = await notesApi.addAttachment(noteId, {
            filename: uploadResult.filename,
            url: uploadResult.url,
            size: uploadResult.size
        });

        setAttachments(prev => [...prev, newAttachment]);
    } catch (err) {
        console.error('Failed to upload attachment', err);
        alert('Failed to upload attachment');
    } finally {
        setIsUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDeleteAttachment = async (attachmentId: string) => {
    if (!note || !window.confirm('Are you sure you want to delete this attachment?')) return;
    
    try {
        await notesApi.deleteAttachment(note.id, attachmentId);
        setAttachments(prev => prev.filter(a => a.id !== attachmentId));
    } catch (err) {
        console.error('Failed to delete attachment', err);
        alert('Failed to delete attachment');
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
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 bg-white p-6 rounded-2xl border border-gray-100 shadow-sm">
            <div className="space-y-5">
                {/* Course Selection */}
                <div className="relative group">
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5 ml-1">Link to Course</label>
                    <div className="relative">
                        <select 
                            value={selectedCourseId}
                            onChange={(e) => setSelectedCourseId(e.target.value)}
                            className="appearance-none block w-full pl-4 pr-10 py-2.5 text-sm border border-gray-200 rounded-xl bg-gray-50/50 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 focus:bg-white transition-all duration-200 cursor-pointer hover:bg-gray-50 hover:border-gray-300"
                        >
                            <option value="">Select a course...</option>
                            {courses.map(course => (
                                <option key={course.id} value={course.id}>{course.title}</option>
                            ))}
                        </select>
                        <ChevronDownIcon />
                    </div>
                </div>

                {/* Topic Selection */}
                <div className="relative group">
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5 ml-1">Link to Topic</label>
                    <div className="relative">
                        <select 
                            value={selectedTopicId}
                            onChange={(e) => setSelectedTopicId(e.target.value)}
                            disabled={!selectedCourseId || loadingStructure}
                            className="appearance-none block w-full pl-4 pr-10 py-2.5 text-sm border border-gray-200 rounded-xl bg-gray-50/50 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 focus:bg-white transition-all duration-200 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed hover:enabled:bg-gray-50 hover:enabled:border-gray-300"
                        >
                            <option value="">Select a topic...</option>
                            {availableTopics.map(topic => (
                                <option key={topic.id} value={topic.id}>
                                    {topic.moduleTitle} - {topic.title}
                                </option>
                            ))}
                        </select>
                        <ChevronDownIcon />
                    </div>
                </div>
            </div>

            <div className="space-y-5">
                {/* Tags */}
                <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5 ml-1">Tags</label>
                    <div className="flex flex-wrap gap-2 items-center min-h-[42px] px-3 py-2 border border-gray-200 rounded-xl bg-gray-50/50 focus-within:ring-2 focus-within:ring-indigo-500/20 focus-within:border-indigo-500 focus-within:bg-white transition-all duration-200">
                        <Tag className="w-4 h-4 text-gray-400 flex-shrink-0 mr-1" />
                        {tags.map(tag => (
                            <span key={tag} className="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">
                                {tag}
                                <button onClick={() => removeTag(tag)} className="ml-1.5 text-indigo-400 hover:text-indigo-900 focus:outline-none">
                                    <X className="w-3 h-3" />
                                </button>
                            </span>
                        ))}
                        <input
                            type="text"
                            value={newTag}
                            onChange={(e) => setNewTag(e.target.value)}
                            onKeyDown={handleAddTag}
                            placeholder={tags.length === 0 ? "Type tag & press Enter..." : ""}
                            className="bg-transparent border-none focus:ring-0 outline-none p-0 text-sm flex-1 min-w-[120px] placeholder-gray-400 text-gray-700"
                        />
                    </div>
                </div>
                
                {/* Timestamp */}
                {!isNew && note && (
                    <div className="flex items-center gap-2 text-xs text-gray-400 pt-1 ml-1">
                        <Calendar className="w-3.5 h-3.5" />
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

        {/* Attachments Section */}
        <div className="mt-6 mb-12">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                    <Paperclip className="w-5 h-5" />
                    Attachments
                </h3>
                <div className="relative">
                    <input
                        type="file"
                        ref={fileInputRef}
                        onChange={handleFileUpload}
                        className="hidden"
                        disabled={isUploading || isSaving}
                    />
                    <button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading || isSaving}
                        className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-indigo-600 bg-indigo-50 rounded-lg hover:bg-indigo-100 transition-colors disabled:opacity-50"
                    >
                        {isUploading ? (
                            <>
                                <Loader className="w-4 h-4 animate-spin" />
                                Uploading...
                            </>
                        ) : (
                            <>
                                <Paperclip className="w-4 h-4" />
                                Add Attachment
                            </>
                        )}
                    </button>
                </div>
            </div>

            {attachments.length === 0 ? (
                <div className="bg-gray-50 border border-gray-200 border-dashed rounded-xl p-8 text-center">
                    <p className="text-gray-500 text-sm">No attachments yet. Upload files to keep them handy.</p>
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {attachments.map(attachment => {
                        const fileType = getFileType(attachment.filename);
                        const FileIcon = getFileIcon(fileType);
                        return (
                        <div key={attachment.id} className="flex items-center p-3 bg-white border border-gray-200 rounded-xl hover:border-indigo-300 transition-colors group">
                            <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg mr-3">
                                <FileIcon className="w-5 h-5" />
                            </div>
                            <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setPreviewFile({ url: attachment.url, name: attachment.filename })}>
                                <p className="block text-sm font-medium text-gray-900 truncate hover:text-indigo-600">
                                    {attachment.filename}
                                </p>
                                <span className="text-xs text-gray-500">
                                    {attachment.size ? `${(attachment.size / 1024).toFixed(1)} KB` : 'Unknown size'} • {new Date(attachment.createdAt).toLocaleDateString()}
                                </span>
                            </div>
                            <div className="flex items-center opacity-0 group-hover:opacity-100 transition-all">
                                <button
                                    onClick={() => setPreviewFile({ url: attachment.url, name: attachment.filename })}
                                    className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg"
                                    title="Preview"
                                >
                                    <Eye className="w-4 h-4" />
                                </button>
                                <button
                                    onClick={() => handleDeleteAttachment(attachment.id)}
                                    className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg"
                                    title="Delete Attachment"
                                >
                                    <X className="w-4 h-4" />
                                </button>
                            </div>
                        </div>
                    )})}
                </div>
            )}
        </div>

        <FilePreviewModal 
            isOpen={!!previewFile}
            onClose={() => setPreviewFile(null)}
            fileUrl={previewFile?.url || ''}
            filename={previewFile?.name || ''}
        />
    </div>
  );
}
