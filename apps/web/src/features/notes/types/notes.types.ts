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
  createdAt: string;
  updatedAt: string;
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

export interface NoteListResponse {
  items: Note[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

