"""SQLAlchemy 2.0 ORM Models - บริษัทเจริญพร VIP Telegram System."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------- Enums ----------

class PackageTier(str, enum.Enum):
    TIER_300 = "300"
    TIER_500 = "500"
    TIER_1299 = "1299"
    TIER_2499 = "2499"


class GroupSlug(str, enum.Enum):
    G300 = "G300"
    G500 = "G500"
    SSS = "SSS"
    VGOD = "VGOD"
    OF = "OF"
    INTER = "INTER"
    SERIES = "SERIES"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    SLIP = "slip"
    PROMPTPAY = "promptpay"
    TRUEWALLET = "truewallet"
    CRYPTO = "crypto"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


class LeadStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    CONVERTED = "converted"
    LOST = "lost"


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class NotificationType(str, enum.Enum):
    PRE_EXPIRY_3D = "pre_expiry_3d"
    PRE_EXPIRY_1D = "pre_expiry_1d"
    EXPIRED = "expired"
    RENEWAL_REMINDER = "renewal_reminder"


# ---------- Models ----------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    referral_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    referred_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    total_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="user", lazy="selectin")
    payments: Mapped[list[Payment]] = relationship(back_populates="user", foreign_keys="[Payment.user_id]", lazy="selectin")
    # admin_logs relationship removed — admin_id stores telegram_id directly
    referrals: Mapped[list[User]] = relationship(back_populates="referrer", lazy="selectin")
    referrer: Mapped[User | None] = relationship(back_populates="referrals", remote_side=[id], lazy="selectin")
    leads: Mapped[list[Lead]] = relationship(back_populates="user", lazy="selectin")
    expiry_notifications: Mapped[list[ExpiryNotification]] = relationship(back_populates="user", lazy="selectin")


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[PackageTier] = mapped_column(Enum(PackageTier), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    groups_access: Mapped[str] = mapped_column(
        Text, nullable=False, comment="comma-separated GroupSlug values"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_members: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="package", lazy="selectin")
    payments: Mapped[list[Payment]] = relationship(back_populates="package", lazy="selectin")

    @property
    def group_list(self) -> list[str]:
        import json as _json
        text = self.groups_access.strip()
        if text.startswith("["):
            try:
                return _json.loads(text)
            except Exception:
                pass
        return [g.strip().strip('"') for g in text.split(",") if g.strip()]


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), nullable=False, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE, nullable=False
    )
    start_date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="subscriptions", lazy="selectin")
    package: Mapped[Package] = relationship(back_populates="subscriptions", lazy="selectin")
    payment: Mapped[Payment | None] = relationship(
        back_populates="subscription", foreign_keys=[payment_id], lazy="selectin"
    )

    __table_args__ = (
        Index("ix_sub_status_end", "status", "end_date"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False
    )
    slip_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    slip_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    slip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    verified_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="payments", foreign_keys=[user_id], lazy="selectin")
    package: Mapped[Package] = relationship(back_populates="payments", lazy="selectin")
    subscription: Mapped[Subscription | None] = relationship(
        back_populates="payment", foreign_keys=[Subscription.payment_id], lazy="selectin"
    )
    # verifier relationship removed — verified_by stores telegram_id directly

    __table_args__ = (
        # UniqueConstraint("slip_hash", name="uq_payment_slip_hash"),  # disabled: admin reviews manually
    )


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # admin relationship removed — admin_id stores telegram_id directly


class GroupRegistry(Base):
    __tablename__ = "group_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[GroupSlug] = mapped_column(Enum(GroupSlug), unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    min_tier: Mapped[PackageTier] = mapped_column(Enum(PackageTier), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BroadcastLog(Base):
    __tablename__ = "broadcast_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_tier: Mapped[PackageTier | None] = mapped_column(Enum(PackageTier), nullable=True)
    target_group: Mapped[GroupSlug | None] = mapped_column(Enum(GroupSlug), nullable=True)
    total_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # admin relationship removed — admin_id stores telegram_id directly


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus), default=LeadStatus.NEW, nullable=False
    )
    interested_tier: Mapped[PackageTier | None] = mapped_column(Enum(PackageTier), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("ad_campaigns.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User | None] = relationship(back_populates="leads", lazy="selectin")
    campaign: Mapped[AdCampaign | None] = relationship(back_populates="leads", lazy="selectin")


class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, comment="telegram,twitter,reddit,etc")
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT, nullable=False
    )
    budget: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    spent: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    leads: Mapped[list[Lead]] = relationship(back_populates="campaign", lazy="selectin")
    performances: Mapped[list[AdPerformance]] = relationship(back_populates="campaign", lazy="selectin")


class AdPerformance(Base):
    __tablename__ = "ad_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    campaign: Mapped[AdCampaign] = relationship(back_populates="performances", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("campaign_id", "date", name="uq_adperf_campaign_date"),
    )


class ApiCostLog(Base):
    __tablename__ = "api_cost_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    cost_thb: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    caller: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="bot/agent name")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_cost_log_date", "created_at"),
    )


class ContentSchedule(Base):
    __tablename__ = "content_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_slug: Mapped[GroupSlug] = mapped_column(Enum(GroupSlug), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="text,photo,video,document")
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    creator: Mapped[User | None] = relationship(lazy="selectin")


class TeaserClick(Base):
    __tablename__ = "teaser_clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    round_time: Mapped[str] = mapped_column(String(10), nullable=False)  # "1230", "1800", "2100", "2300", "0100"
    group_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-10
    converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_teaser_clicks_round", "round_time"),
    )


class GroupMigration(Base):
    __tablename__ = "group_migrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    old_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_group_link: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    member_count_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_count_after_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_count_after_48h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_count_after_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Broadcast(Base):
    """ตาราง broadcasts สำหรับระบบ broadcast ใหม่ (ส่งตรงจาก Admin Bot)."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_photo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="all, group, user, filter")
    target_value: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="group slug, user_id, filter name")
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sent_by_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ContentQueue(Base):
    """รูปที่ authorized users ส่งมาให้ content bot โพสต์เป็น teaser."""

    __tablename__ = "content_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), default="photo", nullable=False, comment="'photo' or 'video'")
    sent_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_content_queue_is_used", "is_used", "created_at"),
    )


class ExpiryNotification(Base):
    __tablename__ = "expiry_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), nullable=False)
    notification_type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="expiry_notifications", lazy="selectin")
    subscription: Mapped[Subscription] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "subscription_id", "notification_type", name="uq_expiry_notif"),
    )
