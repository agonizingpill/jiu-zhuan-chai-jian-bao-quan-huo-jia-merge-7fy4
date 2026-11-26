from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, FiniteFloat, StringConstraints

class SuggestPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stack: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RecordCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ArrangePairs(BaseModel):
    # The workbench accepts a no-body POST as well as optional preview/scope
    # fields.  Preserve unknown fields for route-level compatibility.
    model_config = ConfigDict(extra="allow")

    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    batch: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    batch_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    prefix: Annotated[str, StringConstraints(strip_whitespace=True, max_length=48)] | None = None
    dry_run: bool = False
    preview: bool = False
    stacks: list[Any] | None = None
    codes: list[Any] | None = None
