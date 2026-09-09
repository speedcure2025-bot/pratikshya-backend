import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
_PHONE_RE = re.compile(r"^(?:\+91|0)?[6-9]\d{9}$")


class AddressBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    full_name: str = Field(..., alias="fullName", min_length=1, max_length=255)
    phone: str = Field(..., min_length=10, max_length=20)
    address_line: str = Field(..., alias="addressLine", min_length=1, max_length=500)
    landmark: Optional[str] = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    pincode: str = Field(..., min_length=6, max_length=10)
    address_type: str = Field("Home", alias="type", max_length=50)
    is_default: bool = Field(False, alias="isDefault")

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, v: str) -> str:
        if not _PINCODE_RE.match(v):
            raise ValueError("Pincode must be a valid 6-digit Indian pincode.")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not _PHONE_RE.match(v):
            raise ValueError("Phone must be a valid Indian mobile number.")
        return v


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    """All fields optional for PATCH."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    full_name: Optional[str] = Field(None, alias="fullName", max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    address_line: Optional[str] = Field(None, alias="addressLine", max_length=500)
    landmark: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=10)
    address_type: Optional[str] = Field(None, alias="type", max_length=50)
    is_default: Optional[bool] = Field(None, alias="isDefault")

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _PINCODE_RE.match(v):
            raise ValueError("Pincode must be a valid 6-digit Indian pincode.")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError("Phone must be a valid Indian mobile number.")
        return v


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    full_name: str = Field(serialization_alias="fullName")
    phone: str
    address_line: str = Field(serialization_alias="addressLine")
    landmark: Optional[str] = None
    city: str
    state: str
    pincode: str
    address_type: str = Field(serialization_alias="type")
    is_default: bool = Field(serialization_alias="isDefault")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
