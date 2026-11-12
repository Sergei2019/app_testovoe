"""Модель данных: локации + журнал проводок (источник правды) + балансы (материализация)."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Shop(Base):
    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("shop_id", "sku", name="uq_product_sku"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))


class Warehouse(Base):
    """ФФ. Удаление — архивация: остатки и история сохраняются."""
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    name: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Balance(Base):
    """Остаток локации по товару. reserved осмыслен только для локаций ФФ."""
    __tablename__ = "balances"
    __table_args__ = (
        CheckConstraint("qty >= 0", name="ck_qty_non_negative"),
        CheckConstraint("reserved >= 0", name="ck_reserved_non_negative"),
        CheckConstraint("reserved <= qty", name="ck_reserved_within_qty"),
    )
    location: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class Operation(Base):
    """Заголовок операции журнала.

    UNIQUE(doc_type, doc_id, type) — жёсткая гарантия: каждый переход документа
    создаёт проводки ровно один раз (защита от двойного списания).
    """
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("doc_type", "doc_id", "type", name="uq_document_transition"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    doc_type: Mapped[str | None] = mapped_column(String(16), default=None)
    doc_id: Mapped[int | None] = mapped_column(Integer, default=None)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), default=None)
    qty: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str | None] = mapped_column(String(64), default=None)
    destination: Mapped[str | None] = mapped_column(String(64), default=None)
    comment: Mapped[str | None] = mapped_column(String(400), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Posting(Base):
    """Проводка: дельта остатка конкретной локации в рамках операции."""
    __tablename__ = "postings"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location: Mapped[str] = mapped_column(String(64))
    qty_delta: Mapped[int] = mapped_column(Integer, default=0)
    reserved_delta: Mapped[int] = mapped_column(Integer, default=0)


class Transfer(Base):
    """Перемещение между ФФ: NEW -> SHIPPED -> RECEIVED (или NEW -> CANCELLED)."""
    __tablename__ = "transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    from_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    to_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FbsOrder(Base):
    """Заказ FBS: RESERVED -> SHIPPED -> DELIVERED (или RESERVED -> CANCELLED)."""
    __tablename__ = "fbs_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="RESERVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FboShipment(Base):
    """Отгрузка FBO: NEW -> SHIPPED -> ACCEPTED (или NEW -> CANCELLED)."""
    __tablename__ = "fbo_shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)