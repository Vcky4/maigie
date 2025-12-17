from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class TopicBase(BaseModel):
    title: str
    content: Optional[str] = None
    order: int = 0
    estimatedHours: Optional[float] = None
    completed: bool = False


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    order: Optional[int] = None
    completed: Optional[bool] = None


class TopicResponse(TopicBase):
    id: str
    moduleId: str
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(from_attributes=True)


class ModuleBase(BaseModel):
    title: str
    description: Optional[str] = None
    order: int = 0
    completed: bool = False


class ModuleCreate(ModuleBase):
    topics: List[TopicBase] = []


class ModuleResponse(ModuleBase):
    id: str
    courseId: str
    createdAt: datetime
    updatedAt: datetime
    topics: List[TopicResponse] = []
    model_config = ConfigDict(from_attributes=True)


class CourseBase(BaseModel):
    title: str
    description: Optional[str] = None
    difficulty: str = "Beginner"
    targetDate: Optional[datetime] = None
    isAIGenerated: bool = False
    archived: bool = False


class CourseCreate(CourseBase):
    modules: List[ModuleCreate] = []


class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    difficulty: Optional[str] = None
    targetDate: Optional[datetime] = None
    archived: Optional[bool] = None


class CourseResponse(CourseBase):
    id: str
    userId: str
    progress: float
    createdAt: datetime
    updatedAt: datetime
    modules: List[ModuleResponse] = []
    model_config = ConfigDict(from_attributes=True)
