"""archetypes: request shapes. Both bodies are optional, as the dashboard sends them."""
from pydantic import BaseModel


class AssignIn(BaseModel):
    k: int = 5


class ApplyIn(BaseModel):
    k: int = 10
