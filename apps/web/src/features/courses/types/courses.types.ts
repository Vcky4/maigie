import { Note } from '../../notes/types/notes.types';

export type Difficulty = 'BEGINNER' | 'INTERMEDIATE' | 'ADVANCED' | 'EXPERT';

export interface Topic {
  id: string;
  moduleId: string;
  title: string;
  content?: string;
  order: number;
  completed: boolean;
  estimatedHours?: number;
  note?: Note;
  createdAt: string;
  updatedAt: string;
}

export interface Module {
  id: string;
  courseId: string;
  title: string;
  description?: string;
  order: number;
  completed: boolean;
  topics: Topic[];
  createdAt: string;
  updatedAt: string;
}

export interface Course {
  id: string;
  userId: string;
  title: string;
  description?: string;
  difficulty: Difficulty;
  targetDate?: string;
  isAIGenerated: boolean;
  archived: boolean;
  progress: number;
  totalTopics: number;
  completedTopics: number;
  modules: Module[];
  createdAt: string;
  updatedAt: string;
}

export interface CourseListItem {
  id: string;
  title: string;
  description?: string;
  difficulty: Difficulty;
  progress: number;
  totalModules: number;
  totalTopics: number;
  createdAt: string;
  updatedAt: string;
}

export interface CourseListResponse {
  courses: CourseListItem[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export interface CreateCourseRequest {
  title: string;
  description?: string;
  difficulty: Difficulty;
  isAIGenerated?: boolean;
  targetDate?: string;
}

export interface UpdateCourseRequest {
  title?: string;
  description?: string;
  difficulty?: Difficulty;
  targetDate?: string;
  archived?: boolean;
}
