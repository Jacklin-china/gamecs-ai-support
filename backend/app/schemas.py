from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str
    active: bool


class TicketCreate(BaseModel):
    game_id: int = Field(gt=0)
    version_id: int | None = None
    category_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    ticket_type: Literal["BUG", "ADVICE", "COMPLAINT", "CONSULT"] = "CONSULT"
    priority: int = Field(default=2, ge=1, le=3)


class TicketTransition(BaseModel):
    status: Literal["PROCESSING", "CLOSED", "RATED"]
    remark: str = Field(default="", max_length=500)


class TicketMessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class TicketRatingInput(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class KnowledgeInput(BaseModel):
    game_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    active: bool = True


class RoleUpdate(BaseModel):
    role: Literal["player", "staff", "admin"]
    active: bool = True


class AgentQuestion(BaseModel):
    game_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=128)
