import enum


class Source(str, enum.Enum):
    user_manual = "user_manual"
    whoop_api = "whoop_api"
    agent_inferred = "agent_inferred"
    system_aggregated = "system_aggregated"


class InferredStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"
    expired = "expired"
