import axios from 'axios';
import type {
  Course,
  CourseListResponse,
  CreateCourseRequest,
  UpdateCourseRequest,
  Difficulty,
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
