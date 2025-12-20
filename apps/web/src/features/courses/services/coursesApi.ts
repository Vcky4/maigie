import axios from 'axios';
import type {
  Course,
  CourseListResponse,
  CreateCourseRequest,
  UpdateCourseRequest,
  Difficulty,
  Module,
  Topic,
} from '../types/courses.types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token to requests if available
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export interface CourseListParams {
  page?: number;
  pageSize?: number;
  archived?: boolean;
  difficulty?: string;
  isAIGenerated?: boolean;
  search?: string;
  sortBy?: 'createdAt' | 'updatedAt' | 'title';
  sortOrder?: 'asc' | 'desc';
}

export interface GenerateAICourseRequest {
  topic: string;
  difficulty: Difficulty;
}

export interface GenerateAICourseResponse {
  message: string;
  courseId: string;
  status: string;
}

export interface CreateModuleRequest {
  title: string;
  order: number;
  description?: string;
}

export interface UpdateModuleRequest {
  title?: string;
  order?: number;
  description?: string;
}

export interface CreateTopicRequest {
  title: string;
  order: number;
  content?: string;
  estimatedHours?: number;
}

export interface UpdateTopicRequest {
  title?: string;
  order?: number;
  content?: string;
  estimatedHours?: number;
  completed?: boolean;
}

export const coursesApi = {
  /**
   * List all courses
   */
  listCourses: async (params: CourseListParams = {}): Promise<CourseListResponse> => {
    const response = await apiClient.get<CourseListResponse>('/courses', { params });
    return response.data;
  },

  /**
   * Get course details
   */
  getCourse: async (id: string): Promise<Course> => {
    const response = await apiClient.get<Course>(`/courses/${id}`);
    return response.data;
  },

  /**
   * Create a new course
   */
  createCourse: async (data: CreateCourseRequest): Promise<Course> => {
    const response = await apiClient.post<Course>('/courses', data);
    return response.data;
  },

  /**
   * Generate AI course
   */
  generateAICourse: async (data: GenerateAICourseRequest): Promise<GenerateAICourseResponse> => {
    const response = await apiClient.post<GenerateAICourseResponse>('/courses/generate', data);
    return response.data;
  },

  /**
   * Update a course
   */
  updateCourse: async (id: string, data: UpdateCourseRequest): Promise<Course> => {
    const response = await apiClient.put<Course>(`/courses/${id}`, data);
    return response.data;
  },

  /**
   * Archive a course
   */
  archiveCourse: async (id: string): Promise<void> => {
    await apiClient.post(`/courses/${id}/archive`);
  },

  /**
   * Delete a course
   */
  deleteCourse: async (id: string): Promise<void> => {
    await apiClient.delete(`/courses/${id}`);
  },

  // ==========================================
  // Module Management
  // ==========================================

  createModule: async (courseId: string, data: CreateModuleRequest): Promise<Module> => {
    const response = await apiClient.post<Module>(`/courses/${courseId}/modules`, data);
    return response.data;
  },

  updateModule: async (courseId: string, moduleId: string, data: UpdateModuleRequest): Promise<Module> => {
    const response = await apiClient.put<Module>(`/courses/${courseId}/modules/${moduleId}`, data);
    return response.data;
  },

  deleteModule: async (courseId: string, moduleId: string): Promise<void> => {
    await apiClient.delete(`/courses/${courseId}/modules/${moduleId}`);
  },

  // ==========================================
  // Topic Management
  // ==========================================

  createTopic: async (courseId: string, moduleId: string, data: CreateTopicRequest): Promise<Topic> => {
    const response = await apiClient.post<Topic>(`/courses/${courseId}/modules/${moduleId}/topics`, data);
    return response.data;
  },

  updateTopic: async (courseId: string, moduleId: string, topicId: string, data: UpdateTopicRequest): Promise<Topic> => {
    const response = await apiClient.put<Topic>(`/courses/${courseId}/modules/${moduleId}/topics/${topicId}`, data);
    return response.data;
  },

  deleteTopic: async (courseId: string, moduleId: string, topicId: string): Promise<void> => {
    await apiClient.delete(`/courses/${courseId}/modules/${moduleId}/topics/${topicId}`);
  },

  /**
   * Toggle topic completion
   */
  toggleTopicCompletion: async (courseId: string, moduleId: string, topicId: string, completed: boolean): Promise<void> => {
    await apiClient.patch(
      `/courses/${courseId}/modules/${moduleId}/topics/${topicId}/complete`, 
      null, 
      { params: { completed } }
    );
  },
};
