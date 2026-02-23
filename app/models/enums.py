from enum import StrEnum


class UserType(StrEnum):
    FREE = "free"
    PAID = "paid"
    B2B = "b2b"
    ADMIN = "admin"


class AdminRole(StrEnum):
    MASTER_ADMIN = "master_admin"
    SENIOR_ADMIN = "senior_admin"
    DATA_ADMIN = "data_admin"
    SUPPORT_ADMIN = "support_admin"


class PlanType(StrEnum):
    FREE = "free"
    SINGLE = "single"
    STUDIO = "studio"


class ReportStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportType(StrEnum):
    FREE = "free"
    PAID = "paid"
    B2B = "b2b"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
