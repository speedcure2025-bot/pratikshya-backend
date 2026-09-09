from typing import Any, Dict, Generic, List, Optional, TypeVar, Union
from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class BaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    success: bool = True
    message: Optional[str] = None


class DataResponse(BaseResponse, Generic[T]):
    data: T


ErrorDetails = Union[Dict[str, Any], List[Any]]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: ErrorDetails


class ErrorResponse(BaseModel):
    success: bool
    error: ErrorDetail
