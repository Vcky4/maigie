# Chat-Based Resource Recommendations

## Overview

Users can now chat with the AI to get resource recommendations. Resources are automatically linked to their source (topic/course) when recommended through chat.

## Changes Made

### 1. Database Schema Updates (`prisma/schema.prisma`)

Added fields to `Resource` model:
- `courseId`: Optional link to a course (when resource is recommended for a course)
- `topicId`: Optional link to a topic (when resource is recommended for a topic)
- `recommendationReason`: Text explaining why the resource was recommended (e.g., "for topic X", "related to course Y")

Added relations:
- `Course.resources`: Resources linked to a course
- `Topic.resources`: Resources linked to a topic

### 2. LLM System Instruction Updates (`src/services/llm_service.py`)

Added new action type `recommend_resources`:
- Triggered when users ask for resources, recommendations, suggestions, links, videos, articles, books, etc.
- Extracts query from user's message
- Uses topicId/courseId from context if user is viewing a topic/course
- Default limit of 10 resources

### 3. Action Service Updates (`src/services/action_service.py`)

Added `recommend_resources` action handler:
- Generates recommendations using RAG service
- Links resources to topics/courses when provided
- Stores recommendation reason
- Automatically indexes resources for future searches
- Records interaction in user memory

### 4. Chat Route Updates (`src/routes/chat.py`)

- Detects `recommend_resources` action from AI responses
- Enriches action data with topicId/courseId from context
- Provides user-friendly confirmation messages
- Sends resource data to frontend via WebSocket events

### 5. RAG Service Updates (`src/services/rag_service.py`)

- Includes current topic/course context in recommendation prompts
- Prioritizes resources relevant to current topic/course
- Generates recommendations with clear reasons

## Usage Examples

### Basic Resource Request
```
User: "Can you recommend some resources for learning Python?"
AI: [Generates recommendations and saves them]
Response: "I've found 10 resources for you! They've been saved to your resources."
```

### Topic-Specific Request
```
User (viewing a topic): "What resources should I use for this topic?"
AI: [Generates recommendations linked to the topic]
Response: "I've found 8 resources for [Topic Name]! They've been saved to your resources."
```

### Course-Specific Request
```
User (viewing a course): "Give me some resources for this course"
AI: [Generates recommendations linked to the course]
Response: "I've found 10 resources related to [Course Name]! They've been saved to your resources."
```

## How It Works

1. **User sends message** asking for resources (e.g., "recommend resources for learning React")

2. **AI detects intent** and generates `recommend_resources` action with:
   - Query extracted from user's message
   - topicId/courseId from current context (if available)
   - Limit (default 10)

3. **Action service executes**:
   - Gets user context (courses, goals, recent activity)
   - Enriches with current topic/course if available
   - Calls RAG service to generate recommendations
   - Stores each recommendation as a Resource with:
     - Links to topic/course (if applicable)
     - Recommendation reason
     - Score and metadata
   - Indexes resources for future semantic search
   - Records interaction in user memory

4. **Response sent to user**:
   - Clean conversational response from AI
   - Confirmation message with resource count
   - WebSocket event with resource data for frontend

## Resource Linking

Resources are automatically linked to their source:

- **Topic-linked**: When recommended while viewing a topic
  - `topicId` is set
  - `recommendationReason` includes topic name
  - Can be filtered by topic in UI

- **Course-linked**: When recommended while viewing a course
  - `courseId` is set
  - `recommendationReason` includes course name
  - Can be filtered by course in UI

- **General**: When recommended without specific context
  - No topicId/courseId
  - Recommendation reason based on query
  - Still personalized based on user's overall learning profile

## Frontend Integration

The WebSocket event sent after recommendations includes:
```json
{
  "type": "event",
  "payload": {
    "status": "success",
    "action": "recommend_resources",
    "resources": [
      {
        "id": "...",
        "title": "...",
        "url": "...",
        "description": "...",
        "type": "VIDEO",
        "score": 0.85
      }
    ],
    "count": 10,
    "message": "Successfully generated 10 resource recommendations"
  }
}
```

Frontend can:
- Display resources immediately
- Show them in a resources panel
- Filter by topic/course
- Allow users to bookmark or interact with them

## Benefits

1. **Contextual Recommendations**: Resources are linked to specific topics/courses
2. **Personalized**: Uses RAG and user memory for better recommendations
3. **Conversational**: Natural chat interface instead of separate API calls
4. **Automatic Organization**: Resources are automatically categorized
5. **Searchable**: Indexed for future semantic search
6. **Trackable**: User interactions are recorded for improved future recommendations

## Future Enhancements

1. **Resource Preview**: Show resource metadata (duration, difficulty, etc.) in chat
2. **Resource Actions**: Allow users to bookmark, rate, or dismiss resources directly from chat
3. **Resource Follow-up**: "Show me more like this" functionality
4. **External Integration**: Pull resources from external APIs (YouTube, Coursera, etc.)
5. **Resource Collections**: Group related resources together
