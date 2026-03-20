import contextvars
import uuid

_current_user_id: contextvars.ContextVar[uuid.UUID] = contextvars.ContextVar("current_user_id")


def set_user_id(user_id: uuid.UUID) -> None:
    _current_user_id.set(user_id)


def get_user_id() -> uuid.UUID:
    return _current_user_id.get()
