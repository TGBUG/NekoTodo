from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nekotodo.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    auth_version: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="account")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    custom_prompt_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="user", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    source_infos: Mapped[list["SourceInfo"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    runs: Mapped[list["DecompositionRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="incomplete")
    details: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="tasks")
    source_item: Mapped[Optional["SourceItem"]] = relationship(back_populates="tasks")


class SourceInfo(Base):
    __tablename__ = "source_infos"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="source_infos")
    source_items: Mapped[list["SourceItem"]] = relationship(
        back_populates="source_info", cascade="all, delete-orphan"
    )
    source_files: Mapped[list["SourceFile"]] = relationship(
        back_populates="source_info", cascade="all, delete-orphan"
    )


class SourceItem(Base):
    __tablename__ = "source_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_info_id: Mapped[int] = mapped_column(
        ForeignKey("source_infos.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source_info: Mapped["SourceInfo"] = relationship(back_populates="source_items")
    source_file: Mapped[Optional["SourceFile"]] = relationship(back_populates="source_items")
    tasks: Mapped[list["Task"]] = relationship(back_populates="source_item")


class SourceFile(Base):
    """A file attached to a SourceInfo: an image, or a document.

    Images are described by the VLM into ``description``. Documents are extracted
    into an ordered digest in ``extracted_text`` (image references inline);
    images embedded in a document become their own SourceFile rows of kind
    ``image``, so ordering and provenance survive into the frontend.
    """

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_info_id: Mapped[int] = mapped_column(
        ForeignKey("source_infos.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    file_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="image")
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source_info: Mapped["SourceInfo"] = relationship(back_populates="source_files")
    source_items: Mapped[list["SourceItem"]] = relationship(back_populates="source_file")


class DecompositionRun(Base):
    __tablename__ = "decomposition_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_info_id: Mapped[int] = mapped_column(
        ForeignKey("source_infos.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_task_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="runs")
    source_info: Mapped["SourceInfo"] = relationship(passive_deletes=True)
