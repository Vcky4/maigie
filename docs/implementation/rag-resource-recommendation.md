# RAG-Based Resource Recommendation Implementation

## Overview

This document describes the implementation of AI-powered resource recommendation using RAG (Retrieval-Augmented Generation) and user personalization, based on issue #7.

## Components Implemented

### 1. Database Schema Updates (`prisma/schema.prisma`)

Added three new models:

- **Resource**: Stores educational resources (videos, articles, books, etc.)
  - Tracks recommendation scores and user interactions
  - Supports different resource types (VIDEO, ARTICLE, BOOK, COURSE, etc.)

- **Embedding**: Stores vector embeddings for semantic search
  - Supports multiple object types (resource, note, course, topic)
  - Uses JSON array for vector storage (can be migrated to pgvector later)

- **UserInteractionMemory**: Tracks user interactions for personalization
  - Records clicks, views, bookmarks, feedback
  - Stores importance scores for each interaction
  - Enables personalized recommendations based on user behavior

### 2. Embedding Service (`src/services/embedding_service.py`)

- Generates embeddings using Google Gemini's `text-embedding-004` model
- Stores embeddings in the database
- Implements cosine similarity search for finding relevant content
- Supports both document and query embeddings

### 3. RAG Service (`src/services/rag_service.py`)

- Retrieves relevant context from user's data using semantic search
- Combines retrieved context with LLM generation
- Generates personalized recommendations based on:
  - User's courses and goals
  - Recent activity and interactions
  - Semantic similarity to user's existing content

### 4. User Memory Service (`src/services/user_memory_service.py`)

- Records user interactions (clicks, views, bookmarks, etc.)
- Analyzes interaction patterns to extract preferences
- Provides user context for personalization
- Tracks preferred resource types, active courses, and learning patterns

### 5. Indexing Service (`src/services/indexing_service.py`)

- Automatically indexes content when resources/notes/courses are created/updated
- Can be called as background tasks
- Supports reindexing all user content

### 6. Resource Routes (`src/routes/resources.py`)

- `POST /api/v1/resources/recommend`: Main recommendation endpoint
  - Uses RAG to find relevant content
  - Generates personalized recommendations
  - Records interactions for future personalization

- `POST /api/v1/resources/{id}/interact`: Record user interactions
  - Tracks clicks, views, bookmarks
  - Updates resource statistics
  - Stores interactions in memory for personalization

- `POST /api/v1/resources`: Create new resources
  - Automatically indexes new resources in the background

## How It Works

### Recommendation Flow

1. **User Query**: User requests resources (e.g., "I need resources on machine learning")

2. **Context Retrieval**: 
   - System retrieves user's courses, goals, and recent activity
   - Uses semantic search to find relevant content from user's existing data

3. **RAG Generation**:
   - Combines retrieved context with user query
   - LLM generates recommendations based on:
     - User's learning goals
     - Current courses and topics
     - Past interactions and preferences
     - Semantic similarity to existing content

4. **Personalization**:
   - Recommendations are scored based on relevance
   - User's interaction history influences recommendations
   - Preferred resource types are prioritized

5. **Memory Storage**:
   - Recommendation requests are stored in user memory
   - User interactions with resources are tracked
   - Future recommendations improve based on this data

### Indexing Flow

When content is created or updated:

1. Background task is triggered
2. Content is processed and embedded
3. Embedding is stored in the database
4. Content becomes searchable for future recommendations

## API Usage Examples

### Get Recommendations

```bash
POST /api/v1/resources/recommend
{
  "query": "I want to learn Python programming",
  "limit": 10,
  "context": {
    "courses": [...],
    "goals": [...]
  }
}
```

### Record Interaction

```bash
POST /api/v1/resources/{resource_id}/interact?interaction_type=RESOURCE_CLICK
```

## Personalization Features

- **Learning Pattern Recognition**: Tracks which types of resources users prefer
- **Course Context**: Recommendations align with user's active courses
- **Interaction-Based Scoring**: Frequently accessed resources influence future recommendations
- **Temporal Awareness**: Recent activity has higher weight in recommendations

## Future Enhancements

1. **pgvector Integration**: Migrate from JSON arrays to pgvector for efficient vector similarity search
2. **External Resource Discovery**: Integrate with external APIs (YouTube, Coursera, etc.) for resource discovery
3. **Collaborative Filtering**: Use similar users' preferences to enhance recommendations
4. **Feedback Loop**: Allow users to rate recommendations to improve future results
5. **Resource Curation**: Allow users to save and organize recommended resources

## Database Migration

To apply the schema changes:

```bash
cd apps/backend
npx prisma migrate dev --name add_rag_models
```

## Testing

The implementation includes error handling and logging. To test:

1. Create some resources, notes, or courses
2. Request recommendations via the API
3. Interact with resources to build user memory
4. Request recommendations again to see personalization improvements

## Notes

- Embeddings are stored as JSON arrays. For production, consider migrating to pgvector extension
- The cosine similarity implementation is simplified. For large datasets, use a proper vector database
- Background indexing tasks use FastAPI's BackgroundTasks. For production, consider using Celery workers
- User memory is stored indefinitely. Consider implementing retention policies for GDPR compliance
