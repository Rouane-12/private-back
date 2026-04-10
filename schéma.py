from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class ApartmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    city: str
    price: float
    cover_url: Optional[str] = None

class ApartmentOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    city: str
    price: float
    cover_url: Optional[str]
    is_active: bool

class EventCreate(BaseModel):
    title: str
    date: datetime
    woman_id: int
    apartment_id: int

class EventAction(BaseModel):
    action: str