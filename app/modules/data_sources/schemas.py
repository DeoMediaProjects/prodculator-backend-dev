from pydantic import BaseModel


class DataSourceResponse(BaseModel):
    id: str
    name: str
    slug: str
    category: str
    description: str | None
    endpoint: str | None
    enabled: bool
    status: str
    credential_mode: str
    credential_configured: bool
    is_implemented: bool
    last_tested_at: str | None
    last_test_result: str | None
    last_test_message: str | None
    sync_schedule: str | None
    updated_at: str | None


class DataSourceListResponse(BaseModel):
    items: list[DataSourceResponse]
    total: int
    limit: int
    offset: int


class DataSourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    sync_schedule: str | None = None


class DataSourceTestResponse(BaseModel):
    slug: str
    status: str
    latency_ms: float | None
    message: str
    tested_at: str


class BulkConfigItem(BaseModel):
    id: str
    enabled: bool


class BulkConfigurationRequest(BaseModel):
    sources: list[BulkConfigItem]


class BulkConfigurationResponse(BaseModel):
    updated: int


class SyncScheduleItem(BaseModel):
    slug: str
    name: str
    sync_schedule: str | None
    last_tested_at: str | None
    enabled: bool


class SyncScheduleResponse(BaseModel):
    items: list[SyncScheduleItem]
