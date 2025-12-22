import { FileText, Image, Film, Music, File } from 'lucide-react';

export type FileType = 'image' | 'video' | 'audio' | 'pdf' | 'document' | 'other';

export const getFileType = (filename: string): FileType => {
  const extension = filename.split('.').pop()?.toLowerCase();
  
  if (!extension) return 'other';

  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'];
  const videoExts = ['mp4', 'webm', 'ogg', 'mov', 'avi'];
  const audioExts = ['mp3', 'wav', 'ogg', 'm4a', 'aac'];
  const pdfExts = ['pdf'];
  const docExts = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'rtf', 'md'];

  if (imageExts.includes(extension)) return 'image';
  if (videoExts.includes(extension)) return 'video';
  if (audioExts.includes(extension)) return 'audio';
  if (pdfExts.includes(extension)) return 'pdf';
  if (docExts.includes(extension)) return 'document';
  
  return 'other';
};

export const getFileIcon = (fileType: FileType) => {
  switch (fileType) {
    case 'image': return Image;
    case 'video': return Film;
    case 'audio': return Music;
    case 'pdf': return FileText; // Or a specific PDF icon if available
    case 'document': return FileText;
    default: return File;
  }
};

