from typing import List, Optional
from pydantic import BaseModel, Field


class ItineraryStop(BaseModel):
    place: str = Field(..., example="Sagrada Familia")
    visit_start: str = Field(..., example="09:00")
    visit_end: str = Field(..., example="10:30")


class TransportRequest(BaseModel):
    city: str = Field(..., example="Barcelona")
    allow_walking: bool = Field(default=True, example=True)
    start_point: Optional[str] = Field(default=None, example="Hotel Arts Barcelona")
    itinerary: List[ItineraryStop]


class TransportOption(BaseModel):
    mode: str = Field(..., example="metro")
    duration_min: float = Field(..., example=22.0)
    cost_estimate: float = Field(..., example=2.55)
    walking_distance_m: float = Field(..., example=350.0)
    recommendation_type: Optional[str] = Field(default=None, example="Cheapest")
    reason: Optional[str] = Field(default=None, example="Low-cost and reliable option.")


class TransportLeg(BaseModel):
    from_place: str = Field(..., example="Sagrada Familia")
    to_place: str = Field(..., example="Park Guell")
    options: List[TransportOption]
    recommended_fastest: Optional[TransportOption] = None
    recommended_cheapest: Optional[TransportOption] = None
    recommended_balanced: Optional[TransportOption] = None


class TransportResponse(BaseModel):
    city: str
    legs: List[TransportLeg]
