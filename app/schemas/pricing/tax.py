from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class TaxBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TaxCreate(TaxBase):
    pass


class TaxResponse(TaxBase):
    id: str
