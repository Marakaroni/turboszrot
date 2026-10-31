from __future__ import annotations

from flask_login import UserMixin
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Part(Base):
    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    project_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    condition: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    negotiable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="aktywna")
    vin: Mapped[str] = mapped_column(String(17), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    photo: Mapped[str] = mapped_column(String(255), nullable=True)


class Favorite(Base):
    __tablename__ = "favorites"
    # Jeden uzytkownik moze dodac dana oferte do ulubionych tylko raz.
    __table_args__ = (
        UniqueConstraint("user_id", "part_id", name="uq_favorites_user_part"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class User(UserMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="buyer")
    totp_secret: Mapped[str | None] = mapped_column(String(32), nullable=True)
    totp_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LoginSecurity(Base):
    __tablename__ = "login_security"

    login_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    locked_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    delivery_option: Mapped[str] = mapped_column(String(50), nullable=False, default="standard")
    address: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="nowe")
    total_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    seller_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_reviews_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("part_id", "buyer_id", "seller_id", name="uq_conv_part_buyer_seller"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), nullable=False)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open") # open, closed
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text") # text, offer, accept, reject, system
    price_offer: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
