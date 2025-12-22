import React from 'react';
import { X, Download, ExternalLink } from 'lucide-react';
import { getFileType } from '../../lib/fileUtils';

interface FilePreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  fileUrl: string;
  filename: string;
}

export const FilePreviewModal: React.FC<FilePreviewModalProps> = ({ isOpen, onClose, fileUrl, filename }) => {
  if (!isOpen) return null;

  const fileType = getFileType(filename);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-5xl bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-100">
          <h3 className="text-lg font-semibold text-gray-900 truncate pr-4">{filename}</h3>
          <div className="flex items-center gap-2">
            <a 
              href={fileUrl} 
              download={filename}
              className="p-2 text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              title="Download"
            >
              <Download className="w-5 h-5" />
            </a>
            <a 
              href={fileUrl} 
              target="_blank" 
              rel="noopener noreferrer"
              className="p-2 text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              title="Open in new tab"
            >
              <ExternalLink className="w-5 h-5" />
            </a>
            <button 
              onClick={onClose}
              className="p-2 text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto bg-gray-50 flex items-center justify-center p-4">
          {fileType === 'image' && (
            <img 
              src={fileUrl} 
              alt={filename} 
              className="max-w-full max-h-full object-contain rounded-lg shadow-sm" 
            />
          )}
          
          {fileType === 'video' && (
            <video 
              src={fileUrl} 
              controls 
              className="max-w-full max-h-full rounded-lg shadow-sm"
            >
              Your browser does not support the video tag.
            </video>
          )}
          
          {fileType === 'audio' && (
            <div className="w-full max-w-md p-8 bg-white rounded-xl shadow-sm">
                <audio src={fileUrl} controls className="w-full" />
            </div>
          )}
          
          {fileType === 'pdf' && (
            <iframe 
              src={`${fileUrl}#view=FitH`} 
              title={filename}
              className="w-full h-full min-h-[60vh] rounded-lg shadow-sm border border-gray-200"
            />
          )}
          
          {['document', 'other'].includes(fileType) && (
            <div className="text-center p-12">
              <div className="w-16 h-16 bg-gray-200 rounded-full flex items-center justify-center mx-auto mb-4">
                <Download className="w-8 h-8 text-gray-500" />
              </div>
              <p className="text-gray-900 font-medium mb-2">Preview not available</p>
              <p className="text-gray-500 text-sm mb-6">This file type cannot be previewed directly.</p>
              <a 
                href={fileUrl} 
                download={filename}
                className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
              >
                <Download className="w-4 h-4" />
                Download File
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

