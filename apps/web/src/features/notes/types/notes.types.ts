export interface Note {
  id: string;
  userId: string;
  title: string;
  content: string;
  summary?: string;
  courseId?: string;
  topicId?: string;
  archived: boolean;
  voiceRecordingUrl?: string;
  tags?: NoteTag[];
  attachments?: NoteAttachment[];
  createdAt: string;
  updatedAt: string;
}

export interface NoteTag {
  id: string;
  tag: string;
}

export interface NoteAttachment {
  id: string;
  filename: string;
  url: string;
  size?: number;
  createdAt: string;
}

export interface CreateNoteRequest {
  title: string;
  content?: string;
  courseId?: string;
  topicId?: string;
  tags?: string[];
}

export interface UpdateNoteRequest {
  title?: string;
  content?: string;
  archived?: boolean;
  tags?: string[];
}

export interface CreateNoteAttachmentRequest {
  filename: string;
  url: string;
  size?: number;
}

export interface NoteListResponse {
  items: Note[];
  total: number;
  page: number;
  size: number;
  pages: number;
}
