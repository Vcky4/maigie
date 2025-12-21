import axios from 'axios';
import type { 
  Note, 
  CreateNoteRequest, 
  UpdateNoteRequest, 
  NoteListResponse 
} from '../types/notes.types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export const notesApi = {
  createNote: async (data: CreateNoteRequest): Promise<Note> => {
    const response = await apiClient.post<Note>('/notes/', data);
    return response.data;
  },

  listNotes: async (params: { 
    page?: number; 
    size?: number; 
    courseId?: string; 
    search?: string 
  } = {}): Promise<NoteListResponse> => {
    const response = await apiClient.get<NoteListResponse>('/notes/', { params });
    return response.data;
  },

  getNote: async (id: string): Promise<Note> => {
    const response = await apiClient.get<Note>(`/notes/${id}`);
    return response.data;
  },

  updateNote: async (id: string, data: UpdateNoteRequest): Promise<Note> => {
    const response = await apiClient.put<Note>(`/notes/${id}`, data);
    return response.data;
  },

  deleteNote: async (id: string): Promise<void> => {
    await apiClient.delete(`/notes/${id}`);
  },
};

