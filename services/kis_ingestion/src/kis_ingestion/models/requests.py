import json
from typing import Self

from pydantic import BaseModel, Field


class SubscribeInput(BaseModel):
    tr_id: str
    tr_key: str


class SubscribeHeader(BaseModel):
    approval_key: str
    custtype: str = "P"
    tr_type: str
    content_type: str = Field(default="utf-8", alias="content-type")

    model_config = {"populate_by_name": True}


class SubscribeMessage(BaseModel):
    header: SubscribeHeader
    body: dict[str, SubscribeInput]

    def to_wire(self) -> str:
        return json.dumps(self.model_dump(by_alias=True), ensure_ascii=False)

    @classmethod
    def subscribe(cls, approval_key: str, tr_id: str, stock_code: str) -> Self:
        return cls(
            header=SubscribeHeader(approval_key=approval_key, tr_type="1"),
            body={"input": SubscribeInput(tr_id=tr_id, tr_key=stock_code)},
        )
