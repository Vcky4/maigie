import React, { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom';
import { notesApi } from '../services/notesApi';
import { coursesApi } from '../../courses/services/coursesApi';
import ReactMarkdownOriginal from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Note, NoteAttachment } from '../types/notes.types';
import type { CourseListItem, Course } from '../../courses/types/courses.types';
import { ArrowLeft, Save, Check, Trash2, Calendar, Tag, X, Bold, Italic, List, Heading1, Heading2, Paperclip, Loader, Eye, EyeOff, Mic, MicOff, RefreshCw, Sparkles, Copy } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { getFileIcon, getFileType } from '../../../lib/fileUtils';
import { FilePreviewModal } from '../../../components/common/FilePreviewModal';
import { usePageContext } from '../../courses/contexts/PageContext';

// Workaround for React 18 type definition mismatch
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ReactMarkdown = ReactMarkdownOriginal as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Link = RouterLink as any;

// Web Speech Recognition API types
interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message: string;
}

interface SpeechRecognitionResultList {
  length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionResult {
  length: number;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
  isFinal: boolean;
}

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}

declare global {
  interface Window {
    SpeechRecognition: {
      new (): SpeechRecognition;
    };
    webkitSpeechRecognition: {
      new (): SpeechRecognition;
    };
  }
}

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
  const { setContext, clearContext } = usePageContext();

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
  
  // Editor State
  const [isPreviewMode, setIsPreviewMode] = useState(false);

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

  // Voice transcription state (using Web Speech Recognition API)
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef<SpeechRecognition | null>(null);

  // AI actions state
  const [isRetaking, setIsRetaking] = useState(false);
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [isCopying, setIsCopying] = useState(false);
  const [copyButtonText, setCopyButtonText] = useState('Copy');

  useEffect(() => {
    fetchCourses();
    if (!isNew && noteId) {
      fetchNote(noteId);
    }
  }, [noteId, isNew]);

  // Update page context when note changes
  useEffect(() => {
    if (note) {
      setContext({
        noteId: note.id,
        courseId: note.courseId || undefined,
        topicId: note.topicId || undefined,
      });
    } else if (noteId && !isNew) {
      // Set noteId even if note hasn't loaded yet
      setContext({ noteId });
    }
    return () => {
      clearContext();
    };
  }, [note?.id, note?.courseId, note?.topicId, noteId, isNew, setContext, clearContext]);

  // Adjust textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [content]);

  // Adjust textarea height when switching back to edit mode
  useEffect(() => {
    if (!isPreviewMode && textareaRef.current) {
      // Use setTimeout to ensure the textarea is rendered before calculating height
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = 'auto';
          textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
        }
      }, 0);
    }
  }, [isPreviewMode]);

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

  // Real-time voice transcription using Web Speech Recognition API
  const startVoiceTranscription = () => {
    // Check if browser supports Speech Recognition
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    
    if (!SpeechRecognition) {
      alert('Your browser does not support speech recognition. Please use Chrome, Edge, or Safari.');
      return;
    }

    try {
      const recognition = new SpeechRecognition();
      recognitionRef.current = recognition;

      // Configure recognition
      recognition.continuous = true; // Keep listening
      recognition.interimResults = false; // Only final results (raw speech-to-text)
      recognition.lang = 'en-US'; // Language

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        // Process all results - only final transcripts
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (event.results[i].isFinal) {
            const transcript = event.results[i][0].transcript;
            
            // Update content with final transcript (raw speech-to-text)
            setContent(prev => {
              const newContent = prev + (prev && !prev.endsWith('\n') && !prev.endsWith(' ') ? ' ' : '') + transcript;
              setIsSaved(false);
              if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
              saveTimeoutRef.current = setTimeout(() => handleSave(), 1000);
              return newContent;
            });
          }
        }
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        console.error('Speech recognition error:', event.error);
        if (event.error === 'no-speech') {
          // This is normal, just means no speech detected
          return;
        }
        setIsRecording(false);
      };

      recognition.onend = () => {
        setIsRecording(false);
        recognitionRef.current = null;
      };

      recognition.onstart = () => {
        setIsRecording(true);
      };

      // Start recognition
      recognition.start();
      
    } catch (error) {
      console.error('Error starting speech recognition:', error);
      alert('Could not start speech recognition. Please check permissions.');
      setIsRecording(false);
    }
  };

  const stopVoiceTranscription = () => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    setIsRecording(false);
  };

  const toggleVoiceTranscription = () => {
    if (isRecording) {
      stopVoiceTranscription();
    } else {
      startVoiceTranscription();
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
    };
  }, []);

  // AI action handlers
  const handleRetakeNote = async () => {
    if (!note?.id) {
      alert('Please save the note first before retaking it.');
      return;
    }

    if (!content.trim() && !title.trim()) {
      alert('Please add some content or title before retaking the note.');
      return;
    }

    setIsRetaking(true);
    
    try {
      const updatedNote = await notesApi.retakeNote(note.id);
      setContent(updatedNote.content || '');
      setNote(updatedNote);
      setIsRetaking(false);
      
      // Trigger save indicator
      setIsSaved(true);
      if (savedIndicatorTimeoutRef.current) clearTimeout(savedIndicatorTimeoutRef.current);
      savedIndicatorTimeoutRef.current = setTimeout(() => setIsSaved(false), 2000);
    } catch (error) {
      console.error('Error retaking note:', error);
      setIsRetaking(false);
      alert('Failed to retake note. Please try again.');
    }
  };

  const handleSummarize = async () => {
    if (!note?.id) {
      alert('Please save the note first before adding a summary.');
      return;
    }

    if (!content.trim()) {
      alert('Please add some content before summarizing.');
      return;
    }

    setIsSummarizing(true);
    
    try {
      const updatedNote = await notesApi.addSummaryToNote(note.id);
      setContent(updatedNote.content || '');
      setNote(updatedNote);
      setIsSummarizing(false);
      
      // Trigger save indicator
      setIsSaved(true);
      if (savedIndicatorTimeoutRef.current) clearTimeout(savedIndicatorTimeoutRef.current);
      savedIndicatorTimeoutRef.current = setTimeout(() => setIsSaved(false), 2000);
    } catch (error) {
      console.error('Error generating summary:', error);
      setIsSummarizing(false);
      alert('Failed to generate summary. Please try again.');
    }
  };

  const handleCopyContent = async () => {
    if (!content.trim()) {
      alert('No content to copy.');
      return;
    }

    setIsCopying(true);

    try {
      // Copy content to clipboard
      await navigator.clipboard.writeText(content);
      setIsCopying(false);

      // Show temporary success feedback
      setCopyButtonText('Copied!');
      setTimeout(() => {
        setCopyButtonText('Copy');
      }, 2000);
    } catch (error) {
      console.error('Error copying to clipboard:', error);
      setIsCopying(false);
      alert('Failed to copy content. Please try again.');
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
  const insertFormat = (prefix: string, suffix = '') => {
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
            
            <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                <div className="h-6 flex items-center">
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
                    <span className="hidden sm:inline">Create Note</span>
                    <span className="sm:hidden">Create</span>
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

      {/* Summary Section */}
      {note?.summary && (
        <div className="mb-6 bg-gradient-to-r from-indigo-50 to-purple-50 rounded-xl border border-indigo-200 shadow-sm p-6">
          <div className="flex items-start gap-3">
            <Sparkles className="w-5 h-5 text-indigo-600 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-indigo-900 uppercase tracking-wider mb-2">
                Summary
              </h3>
              <div className="prose prose-sm prose-indigo max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {note.summary}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Editor Toolbar */}
      <div className="flex items-center justify-between p-2 bg-gray-50 border border-gray-200 border-b-0 rounded-t-xl overflow-x-auto">
        <div className="flex items-center gap-1">
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
            <div className="w-px h-4 bg-gray-300 mx-1" />
            <button 
              onClick={toggleVoiceTranscription}
              className={cn(
                "p-1.5 rounded transition-colors relative",
                isRecording 
                  ? "text-red-600 hover:bg-red-50 bg-red-50" 
                  : "text-gray-600 hover:bg-gray-200"
              )}
              title={isRecording ? "Stop voice transcription" : "Start voice transcription"}
            >
              {isRecording ? (
                <MicOff className="w-4 h-4" />
              ) : (
                <Mic className="w-4 h-4" />
              )}
              {isRecording && (
                <span className="absolute top-0 right-0 w-2 h-2 bg-red-500 rounded-full animate-pulse" />
              )}
            </button>
            {isRecording && (
              <div className="flex items-center gap-1 text-xs text-gray-500 px-2">
                <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
                <span>Listening...</span>
              </div>
            )}
            <div className="w-px h-4 bg-gray-300 mx-1" />
            <button 
              onClick={handleRetakeNote}
              disabled={isRetaking || isSummarizing}
              className={cn(
                "p-1.5 rounded transition-colors flex items-center gap-1",
                isRetaking 
                  ? "text-indigo-600 bg-indigo-50" 
                  : "text-gray-600 hover:bg-gray-200"
              )}
              title="Retake Note (AI will regenerate content)"
            >
              <RefreshCw className={cn("w-4 h-4", isRetaking && "animate-spin")} />
              <span className="text-xs hidden sm:inline">Retake</span>
            </button>
            <button 
              onClick={handleSummarize}
              disabled={isRetaking || isSummarizing}
              className={cn(
                "p-1.5 rounded transition-colors flex items-center gap-1",
                isSummarizing 
                  ? "text-indigo-600 bg-indigo-50" 
                  : "text-gray-600 hover:bg-gray-200"
              )}
              title="Generate Summary"
            >
              <Sparkles className={cn("w-4 h-4", isSummarizing && "animate-pulse")} />
              <span className="text-xs hidden sm:inline">Summarize</span>
            </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopyContent}
            disabled={isCopying}
            className="p-1.5 rounded transition-colors flex items-center gap-1 font-medium whitespace-nowrap text-gray-600 hover:bg-gray-200"
          >
            <Copy className="w-3.5 h-3.5" />
            <span className="hidden sm:inline text-xs">{copyButtonText}</span>
          </button>
          <button
            onClick={() => setIsPreviewMode(!isPreviewMode)}
            className={cn(
              "flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors whitespace-nowrap self-start sm:self-auto",
              isPreviewMode
                ? "bg-indigo-100 text-indigo-700"
                : "text-gray-600 hover:bg-gray-200"
            )}
          >
            {isPreviewMode ? (
              <>
                <EyeOff className="w-3.5 h-3.5" />
                <span>Edit</span>
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5" />
                <span>Preview</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 bg-white rounded-b-xl border border-gray-200 shadow-sm mb-8 relative min-h-[400px]">
        {isPreviewMode ? (
            <div className="w-full h-full p-8 prose prose-indigo max-w-none overflow-y-auto prose-p:my-1 prose-headings:my-2 prose-ul:my-2 prose-li:my-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {content || '*No content*'}
                </ReactMarkdown>
            </div>
        ) : (
            <textarea
                ref={textareaRef}
                value={content}
                onChange={handleContentChange}
                placeholder="Start typing your notes here... (Markdown supported)"
                className="w-full h-full p-8 resize-none border-none outline-none text-base sm:text-lg text-gray-800 leading-relaxed font-sans bg-transparent placeholder-gray-300 font-mono"
                spellCheck={false}
            />
        )}
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
