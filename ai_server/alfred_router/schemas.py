"""
Pydantic models for Alfred router decisions.

The router must return exactly one of the defined shapes. Validation errors
are surfaced directly; no heuristic parsing is allowed.
"""
from typing import Any, Dict, Literal, Union
from pydantic import BaseModel, Field, ConfigDict


class CallToolDecision(BaseModel):
    """Router decided to call an existing tool/integration."""

    intent: Literal["call_tool"]
    tool: str = Field(..., description="Name of the tool or integration to call")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Structured parameters for the tool"
    )

    model_config = ConfigDict(extra="forbid")


class RouteToQADecision(BaseModel):
    """Router decided to route the request to the read-only Q/A model."""

    intent: Literal["route_to_qa"]
    query: str = Field(..., description="User query for Q/A handling")

    model_config = ConfigDict(extra="forbid")


class ProposeNewToolDecision(BaseModel):
    """
    Router proposes a new tool/plugin.

    SAFETY: This is strictly non-executable. It must only return a proposal
    object and must never write files, register plugins, or modify runtime
    state. Execution remains the responsibility of a human or a separate
    vetted process.
    """

    intent: Literal["propose_new_tool"]
    name: str = Field(..., description="Proposed tool name")
    description: str = Field(..., description="What the proposed tool would do")

    model_config = ConfigDict(extra="forbid")


RouterDecision = Union[CallToolDecision, RouteToQADecision, ProposeNewToolDecision]



