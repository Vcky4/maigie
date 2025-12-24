import React, { useEffect, useState, useRef } from 'react';
import { useParams, Link as RouterLink } from 'react-router-dom';
import { coursesApi } from '../services/coursesApi';
import { notesApi } from '../../notes/services/notesApi';
import { chatApi } from '../services/chatApi';
import ReactMarkdownOriginal from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Course, Topic } from '../types/courses.types';
import type { Note, NoteAttachment } from '../../notes/types/notes.types';
import { ArrowLeft, CheckCircle, Circle, ChevronRight, ChevronLeft, Save, Check, Brain, FileText, Bold, Italic, List, Heading1, Heading2, Paperclip, Loader, X, Eye, EyeOff, Mic, MicOff } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { getFileIcon, getFileType } from '../../../lib/fileUtils';
import { FilePreviewModal } from '../../../components/common/FilePreviewModal';

// Workaround for React 18 type definition mismatch with react-router-dom and react-markdown
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Link = RouterLink as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ReactMarkdown = ReactMarkdownOriginal as any;

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
  
  // Attachments state
  const [attachments, setAttachments] = useState<NoteAttachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  
  // Editor State
  const [isPreviewMode, setIsPreviewMode] = useState(false);
  
  // Preview Modal State
  const [previewFile, setPreviewFile] = useState<{ url: string; name: string } | null>(null);

  // Voice transcription state
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const transcriptionQueueRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    if (courseId) {
      fetchCourse(courseId);
    }
  }, [courseId]);

  // Listen for AI action events to refetch data
  useEffect(() => {
    if (!courseId) return;
    
    const handleActionEvent = (event: Event) => {
      const customEvent = event as CustomEvent;
      const { action, status, payload } = customEvent.detail;
      
      // If a note was created for this topic, refetch the course
      if (action === 'create_note' && status === 'success') {
        if (payload?.note_id) {
          // Refetch course to get updated note data
          fetchCourse(courseId);
          // Also fetch note details if we have the note ID
          fetchNoteDetails(payload.note_id);
        }
      }
    };

    window.addEventListener('aiActionCompleted', handleActionEvent);
    return () => {
      window.removeEventListener('aiActionCompleted', handleActionEvent);
    };
  }, [courseId]);

  useEffect(() => {
    if (course && moduleId && topicId) {
      const module = course.modules.find(m => m.id === moduleId);
      const topic = module?.topics.find(t => t.id === topicId);
      
      if (topic) {
        setCurrentTopic(topic);
        
        // Use note content if available, fallback to topic content (migration support), or empty
        // Initially set content from the topic's embedded note or topic content
        const initialContent = topic.note?.content || topic.content || '';
        setContent(initialContent);
        
        if (topic.note) {
            // Fetch fresh note data to ensure we have latest attachments
            // The course object might be stale or missing nested relations if not requested
            setCurrentNote(topic.note); // Set initial state from course
            fetchNoteDetails(topic.note.id);
        } else {
            setCurrentNote(null);
            setAttachments([]);
        }
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

  const fetchNoteDetails = async (noteId: string) => {
    try {
        const freshNote = await notesApi.getNote(noteId);
        setCurrentNote(freshNote);
        setAttachments(freshNote.attachments || []);
        // Also update content if it's different (optional, but good for sync)
        if (freshNote.content) {
            setContent(freshNote.content);
        }
    } catch (err) {
        console.error('Failed to fetch fresh note details', err);
        // Fallback to what we have is already set in the useEffect
    }
  };

  const saveContent = async (newContent: string) => {
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
    saveTimeoutRef.current = setTimeout(() => saveContent(newContent), 1000);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0 || !courseId || !topicId || !currentTopic) return;
    
    // Auto-save the note if it's new before uploading attachment
    if (!currentNote) {
        setIsSaving(true);
        try {
            // Create new note
            const newNote = await notesApi.createNote({
                title: currentTopic.title,
                content: content, // Use current content
                courseId: courseId,
                topicId: topicId,
            });
            setCurrentNote(newNote);
            
            // Continue with upload using the new note ID
            await uploadAttachment(newNote.id, e.target.files[0]);
        } catch (err) {
            console.error('Failed to create note for upload', err);
            alert('Failed to save note before upload.');
        } finally {
            setIsSaving(false);
        }
    } else {
        await uploadAttachment(currentNote.id, e.target.files[0]);
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
    if (!currentNote || !window.confirm('Are you sure you want to delete this attachment?')) return;
    
    try {
        await notesApi.deleteAttachment(currentNote.id, attachmentId);
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
    setIsSaved(false);
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    saveTimeoutRef.current = setTimeout(() => saveContent(newContent), 1000);
  };

  const handleStudy = () => {
    // Placeholder for AI study feature
    console.log('Study feature triggered');
  };

  const handleResources = () => {
    // Placeholder for resources
    console.log('Resources clicked');
  };

  // Real-time voice transcription
  const startVoiceTranscription = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
      
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm;codecs=opus'
      });
      mediaRecorderRef.current = mediaRecorder;

      const audioChunks: Blob[] = [];
      const CHUNK_INTERVAL_MS = 3000; // Send chunks every 3 seconds

      mediaRecorder.ondataavailable = async (event) => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
          
          // Create a chunk file and transcribe it
          const chunkBlob = new Blob([event.data], { type: 'audio/webm' });
          const chunkFile = new File([chunkBlob], `chunk-${Date.now()}.webm`, { type: 'audio/webm' });
          
          // Queue transcription to avoid race conditions
          transcriptionQueueRef.current = transcriptionQueueRef.current.then(async () => {
            try {
              setIsTranscribing(true);
              const result = await chatApi.transcribeVoice(chunkFile);
              
              if (result.text && result.text.trim()) {
                // Append transcribed text to content (only new text, no accumulation)
                const transcribedText = result.text.trim();
                
                // Update content by appending only the new transcribed text
                setContent(prev => {
                  const newContent = prev + (prev && !prev.endsWith('\n') && !prev.endsWith(' ') ? ' ' : '') + transcribedText;
                  // Trigger save
                  setIsSaved(false);
                  if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
                  saveTimeoutRef.current = setTimeout(() => saveContent(newContent), 1000);
                  return newContent;
                });
              }
            } catch (error) {
              console.error('Transcription error:', error);
              // Continue recording even if one chunk fails
            } finally {
              setIsTranscribing(false);
            }
          });
        }
      };

      mediaRecorder.onstop = () => {
        // Process any remaining chunks
        if (audioChunks.length > 0) {
          const finalBlob = new Blob(audioChunks, { type: 'audio/webm' });
          const finalFile = new File([finalBlob], 'final-chunk.webm', { type: 'audio/webm' });
          
          transcriptionQueueRef.current = transcriptionQueueRef.current.then(async () => {
            try {
              setIsTranscribing(true);
              const result = await chatApi.transcribeVoice(finalFile);
              
              if (result.text && result.text.trim()) {
                // Append transcribed text to content (only new text, no accumulation)
                const transcribedText = result.text.trim();
                
                setContent(prev => {
                  const newContent = prev + (prev && !prev.endsWith('\n') && !prev.endsWith(' ') ? ' ' : '') + transcribedText;
                  setIsSaved(false);
                  if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
                  saveTimeoutRef.current = setTimeout(() => saveContent(newContent), 1000);
                  return newContent;
                });
              }
            } catch (error) {
              console.error('Final transcription error:', error);
            } finally {
              setIsTranscribing(false);
            }
          });
        }
        
        // Stop all tracks
        stream.getTracks().forEach(track => track.stop());
        audioStreamRef.current = null;
        setIsRecording(false);
      };

      // Start recording with timeslices for chunked transcription
      mediaRecorder.start(CHUNK_INTERVAL_MS);
      setIsRecording(true);
      
    } catch (error) {
      console.error('Error accessing microphone:', error);
      alert('Could not access microphone. Please check permissions.');
      setIsRecording(false);
    }
  };

  const stopVoiceTranscription = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach(track => track.stop());
      audioStreamRef.current = null;
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
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      if (audioStreamRef.current) {
        audioStreamRef.current.getTracks().forEach(track => track.stop());
      }
    };
  }, []);

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
            {isTranscribing && (
              <div className="flex items-center gap-1 text-xs text-gray-500 px-2">
                <Loader className="w-3 h-3 animate-spin" />
                <span>Transcribing...</span>
              </div>
            )}
        </div>

        <button 
            onClick={() => setIsPreviewMode(!isPreviewMode)} 
            className={cn(
                "flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors",
                isPreviewMode 
                    ? "bg-indigo-100 text-indigo-700" 
                    : "text-gray-600 hover:bg-gray-200"
            )}
        >
            {isPreviewMode ? (
                <>
                    <EyeOff className="w-3.5 h-3.5" />
                    Edit
                </>
            ) : (
                <>
                    <Eye className="w-3.5 h-3.5" />
                    Preview
                </>
            )}
        </button>
      </div>

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
      <div className="mb-8 bg-white rounded-xl border border-gray-200 shadow-sm p-6">
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
                <p className="text-gray-500 text-sm">No attachments yet. Upload files related to this topic.</p>
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
