# Notes Module

## Issue Type
Feature

## Priority
Medium

## Labels
- notes
- content
- productivity
- mvp

## Description

Implement the notes management system where users can create, edit, organize, and search their study notes. Supports rich text editing, AI summarization, voice-to-text, and automatic linking to courses.

## User Stories

### As a user:
- I want to create notes for my study sessions
- I want to format my notes with rich text
- I want AI to summarize my notes
- I want to use voice-to-text for quick note-taking
- I want my notes automatically linked to relevant courses
- I want to search across all my notes

## Functional Requirements

### Note Structure
- Note contains:
  - Title
  - Content (rich text)
  - Created date
  - Modified date
  - Tags
  - Related course (optional)
  - Related topic (optional)
  - AI-generated summary (optional)
  - Attachments (optional)

### Note Creation
- Manual creation via editor
- Quick note from voice input
- AI-assisted note generation
- Import from external sources (optional)

### Rich Text Editor
- Text formatting (bold, italic, underline)
- Headers (H1, H2, H3)
- Lists (ordered, unordered)
- Code blocks
- Quotes
- Links
- Tables
- Images (optional)
- Latex/mathematical formulas (optional)

### Note Management
- View all notes (list or grid)
- Search notes by content or title
- Filter by course, topic, or tags
- Sort by date, title, or relevance
- Archive old notes
- Delete notes (with confirmation)
- Duplicate notes

### AI Features
- **Summarize notes**: AI creates concise summaries
- **Key points extraction**: AI highlights main ideas
- **Question generation**: AI creates study questions from notes
- **Explain concepts**: AI clarifies difficult topics
- **Auto-tagging**: AI suggests relevant tags
- **Auto-linking**: AI suggests course associations

### Voice-to-Text
- Record voice notes
- Real-time transcription
- Edit transcribed text
- Voice note playback

### Organization
- Tags for categorization
- Folder structure (optional)
- Favorites/starred notes
- Recently viewed notes
- Course-based organization

### Search & Discovery
- Full-text search
- Tag-based filtering
- Date range filtering
- Course filtering
- Search within note

## Technical Requirements

### Backend
- FastAPI endpoints for CRUD operations
- Prisma model for Note
- Full-text search (PostgreSQL or Elasticsearch)
- AI summarization service
- Speech-to-text integration (Whisper)
- File storage for attachments (S3 or similar)
- WebSocket for real-time collaboration (future)

### Frontend (Web - Vite + shadcn-ui)
- Rich text editor component (TipTap or similar)
- Note list with search
- Note detail view
- Voice recording interface
- AI feature buttons
- Tag management UI
- Markdown preview (optional)

### Frontend (Mobile - Expo)
- Native note editor
- Voice recording with native APIs
- Offline note creation and editing
- Sync mechanism for offline changes
- Camera integration for note images

### Database Schema
```
Note {
  id, userId, title, content,
  summary, createdAt, updatedAt,
  courseId, topicId, archived,
  voiceRecordingUrl
}

NoteTag {
  id, noteId, tag
}

NoteAttachment {
  id, noteId, filename, url, size
}
```

## Acceptance Criteria

- [ ] User can create new note
- [ ] User can edit note content
- [ ] Rich text formatting works (bold, italic, lists, etc.)
- [ ] User can save notes
- [ ] Auto-save works every 30 seconds
- [ ] User can view list of all notes
- [ ] User can search notes by title or content
- [ ] Search results are relevant and fast (< 500ms)
- [ ] User can add tags to notes
- [ ] User can filter notes by tags
- [ ] User can link notes to courses
- [ ] User can record voice notes
- [ ] Voice-to-text transcription works accurately
- [ ] AI can summarize notes
- [ ] AI summaries are accurate and concise
- [ ] User can delete notes
- [ ] User can archive notes
- [ ] Notes sync across web and mobile
- [ ] Offline note editing works on mobile
- [ ] Changes sync when back online
- [ ] Note editor is responsive and fast
- [ ] Images can be added to notes (optional)
- [ ] Attachments can be uploaded (optional)

## API Endpoints

- `GET /api/notes` - List all user notes
- `POST /api/notes` - Create new note
- `GET /api/notes/:id` - Get note details
- `PUT /api/notes/:id` - Update note
- `DELETE /api/notes/:id` - Delete note
- `POST /api/notes/:id/archive` - Archive note
- `POST /api/notes/:id/summarize` - AI summarize note
- `POST /api/notes/voice` - Upload voice note for transcription
- `GET /api/notes/search` - Search notes
- `POST /api/notes/:id/tags` - Add tags
- `DELETE /api/notes/:id/tags/:tag` - Remove tag
- `POST /api/notes/:id/attachments` - Upload attachment

## UI Components

- NoteList
- NoteCard
- NoteEditor (rich text)
- SearchBar
- TagFilter
- TagManager
- VoiceRecorder
- SummaryPanel
- AttachmentUploader
- NoteSidebar

## Dependencies

- AI Chat (for AI features)
- Courses Module (note-course linking)
- Speech-to-text service (Whisper)
- Rich text editor library
- File storage service

## Performance Requirements

- Note list loads in < 1 second
- Note editor opens in < 500ms
- Search results in < 500ms
- Auto-save without lag
- Support 1000+ notes per user
- Voice transcription < 3 seconds

## Security Considerations

- User can only access own notes
- Authorization checks on all endpoints
- Secure file upload validation
- XSS prevention in rich text content
- Rate limiting on AI features

## Testing Requirements

- Unit tests for note operations
- Integration tests for CRUD operations
- E2E tests for note creation and editing
- Search functionality tests
- Voice transcription tests
- AI summarization tests
- Offline sync tests for mobile

## Estimated Effort
Medium - 2-3 sprints

## Related Issues
- AI Chat (AI note features)
- Courses Module (note-course linking)
- Dashboard (notes preview)
