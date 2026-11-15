from pydantic import BaseModel, Field


class StallInput(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)


class DishInput(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(default='份', max_length=16)


class PlanInput(BaseModel):
    data_date: str = Field(min_length=8, max_length=10, pattern=r'^\d{4}-\d{2}-\d{2}$')
    dish_id: int
    stall_id: int
    suggested_qty: int = Field(gt=0)
    approved_job_id: int | None = None
    note: str = Field(default='', max_length=300)


class PrepEntryInput(BaseModel):
    plan_id: int
    prepared_qty: int = Field(gt=0)
    note: str = Field(default='', max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=80)


class ConsumptionInput(BaseModel):
    entry_id: int
    qty: int = Field(gt=0)
    purpose: str = Field(default='', max_length=160)
    idempotency_key: str | None = Field(default=None, max_length=80)


class TransferOrderInput(BaseModel):
    dish_id: int
    from_stall_id: int
    to_stall_id: int
    request_qty: int = Field(gt=0)
    plan_id: int | None = None
    note: str = Field(default='', max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=80)


class TransferActionInput(BaseModel):
    qty: int = Field(gt=0)
    idempotency_key: str | None = Field(default=None, max_length=80)
