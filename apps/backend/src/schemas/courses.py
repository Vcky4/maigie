from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class TopicBase(BaseModel):
    title: str
    content: str | None = None
    order: int = 0
    estimatedHours: float | None = None
    completed: bool = False


class TopicUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    order: int | None = None
    completed: bool | None = None


class TopicResponse(TopicBase):
    id: str
    moduleId: str
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(from_attributes=True)


class ModuleBase(BaseModel):
    title: str
    description: str | None = None
    order: int = 0
    completed: bool = False


class ModuleCreate(ModuleBase):
    topics: list[TopicBase] = []


class ModuleResponse(ModuleBase):
    id: str
    courseId: str
    createdAt: datetime
    updatedAt: datetime
    topics: list[TopicResponse] = []
    model_config = ConfigDict(from_attributes=True)


class CourseBase(BaseModel):
    title: str
    description: str | None = None
    difficulty: str = "Beginner"
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    archived: bool = False


class CourseCreate(CourseBase):
    modules: list[ModuleCreate] = []


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    difficulty: str | None = None
    targetDate: datetime | None = None
    archived: bool | None = None


class CourseResponse(CourseBase):
    id: str
    userId: str
    progress: float
    createdAt: datetime
    updatedAt: datetime
    modules: list[ModuleResponse] = []
    model_config = ConfigDict(from_attributes=True)
