import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MealCatalog(Base):
    __tablename__ = "meal_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    date: Mapped[date] = mapped_column(Date)
    meal_number: Mapped[int] = mapped_column(Integer)  # порядок на скрине (1, 2, 3...)
    name: Mapped[str] = mapped_column(Text)
    calories: Mapped[int | None] = mapped_column(Integer)
    protein_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="delivery_menu")  # level_kitchen, growfood, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
