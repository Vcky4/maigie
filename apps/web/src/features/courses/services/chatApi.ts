/**
 * Chat/AI API service
 */

import axios from 'axios';

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

export interface VoiceTranscriptionResponse {
  text: string;
}

export const chatApi = {
  /**
   * Transcribe audio file to text
   */
  transcribeVoice: async (audioFile: File): Promise<VoiceTranscriptionResponse> => {
    const token = localStorage.getItem('access_token');
    if (!token) {
      throw new Error('Authentication required');
    }

    const formData = new FormData();
    formData.append('file', audioFile);

    const response = await apiClient.post<VoiceTranscriptionResponse>(
      '/chat/voice',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        params: {
          token,
        },
      }
    );
    return response.data;
  },
};

/**
 * WebSocket client for real-time AI chat
 */
export class ChatWebSocketClient {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();
  private isConnecting = false;

  constructor(
    private onMessage: (message: string) => void,
    private onError?: (error: Error) => void,
    private onConnect?: () => void,
    private onDisconnect?: () => void
  ) {}

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.isConnecting) {
      return;
    }

    const token = localStorage.getItem('access_token');
    if (!token) {
      this.onError?.(new Error('Authentication required'));
      return;
    }

    this.isConnecting = true;
    // Convert HTTP/HTTPS URL to WebSocket URL
    let wsUrl = API_BASE_URL || 'http://localhost:8000/api/v1';
    
    // Remove trailing /api/v1 if present (since API_BASE_URL already includes it)
    if (wsUrl.endsWith('/api/v1')) {
      wsUrl = wsUrl.replace('/api/v1', '');
    }
    
    // Convert protocol
    if (wsUrl.startsWith('https://')) {
      wsUrl = wsUrl.replace('https://', 'wss://');
    } else if (wsUrl.startsWith('http://')) {
      wsUrl = wsUrl.replace('http://', 'ws://');
    } else {
      // Fallback if URL doesn't start with http/https
      wsUrl = 'ws://localhost:8000';
    }
    
    // Append the chat WebSocket endpoint
    const url = `${wsUrl}/api/v1/chat/ws?token=${encodeURIComponent(token)}`;

    try {
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.onConnect?.();
      };

      this.ws.onmessage = (event) => {
        try {
          // Check if it's JSON (for events) or plain text (for messages)
          if (event.data.startsWith('{')) {
            const data = JSON.parse(event.data);
            if (data.type === 'event') {
              // Handle action events (e.g., course created)
              this.emit('event', data.payload);
            } else {
              // Handle other JSON messages
              this.emit('message', data);
            }
          } else {
            // Plain text message from AI
            this.onMessage(event.data);
          }
        } catch (e) {
          // If parsing fails, treat as plain text
          this.onMessage(event.data);
        }
      };

      this.ws.onerror = (error) => {
        this.isConnecting = false;
        this.onError?.(new Error('WebSocket connection error'));
      };

      this.ws.onclose = () => {
        this.isConnecting = false;
        this.onDisconnect?.();
        this.attemptReconnect();
      };
    } catch (error) {
      this.isConnecting = false;
      this.onError?.(error as Error);
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.onError?.(new Error('Max reconnection attempts reached'));
      return;
    }

    this.reconnectAttempts++;
    setTimeout(() => {
      if (this.ws?.readyState !== WebSocket.OPEN) {
        this.connect();
      }
    }, this.reconnectDelay * this.reconnectAttempts);
  }

  send(message: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(message);
    } else {
      this.onError?.(new Error('WebSocket is not connected'));
    }
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.reconnectAttempts = 0;
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  on(event: string, callback: (data: any) => void): void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)?.add(callback);
  }

  off(event: string, callback: (data: any) => void): void {
    this.listeners.get(event)?.delete(callback);
  }

  private emit(event: string, data: any): void {
    this.listeners.get(event)?.forEach((callback) => callback(data));
  }
}

