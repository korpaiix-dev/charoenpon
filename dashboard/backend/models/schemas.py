"""Pydantic schemas for request/response validation."""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# --- Flash Sale ---
class FlashSaleCreate(BaseModel):
    name: str
    package_id: int
    flash_price: float
    original_price: float
    total_slots: int = 30
    starts_at: str  # ISO datetime
    ends_at: str

class FlashSaleUpdate(BaseModel):
    name: Optional[str] = None
    flash_price: Optional[float] = None
    total_slots: Optional[int] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    is_active: Optional[bool] = None

# --- Promo Code ---
class PromoCodeCreate(BaseModel):
    code: str
    discount_pct: int
    max_uses: int = 1
    package_id: Optional[int] = None
    min_amount: Optional[float] = None
    expires_at: str

class PromoCodeUpdate(BaseModel):
    discount_pct: Optional[int] = None
    max_uses: Optional[int] = None
    is_active: Optional[bool] = None
    expires_at: Optional[str] = None

# --- Scheduled Promotion ---
class ScheduledPromotionCreate(BaseModel):
    name: str
    message_text: str
    target_groups: List[str]
    scheduled_at: str
    repeat_type: str = "once"

class ScheduledPromotionUpdate(BaseModel):
    name: Optional[str] = None
    message_text: Optional[str] = None
    target_groups: Optional[List[str]] = None
    scheduled_at: Optional[str] = None
    repeat_type: Optional[str] = None
    is_active: Optional[bool] = None

# --- Customer Actions ---
class ExtendRequest(BaseModel):
    days: int

class UpgradeRequest(BaseModel):
    package_id: int

class KickRequest(BaseModel):
    group_ids: List[int]

class BanRequest(BaseModel):
    reason: str = ""

class DMRequest(BaseModel):
    message: str

# --- Team ---
class TeamMemberCreate(BaseModel):
    telegram_id: int
    display_name: str
    password: str
    role: str = "moderator"

class TeamMemberUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

# --- Group ---
class GroupCreate(BaseModel):
    slug: str
    chat_id: int
    title: str
    min_tier: str
    is_active: bool = True

class GroupUpdate(BaseModel):
    title: Optional[str] = None
    min_tier: Optional[str] = None
    is_active: Optional[bool] = None

# --- Package ---
class PackageCreate(BaseModel):
    name: str
    tier: str
    price: float
    duration_days: int
    description: str = ""
    groups_access: str = "[]"
    is_active: bool = True
    sort_order: int = 99

class PackageUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    duration_days: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None

# --- Payment ---
class PaymentReject(BaseModel):
    reason: str = ""

# --- Promotion Campaign Center ---
class PromotionCampaignCreate(BaseModel):
    name: str
    package_id: int
    normal_price: float
    promo_price: float
    starts_at: str
    ends_at: str
    bot_badge: str = ""
    bot_sales_text: str = ""
    group_caption: str = ""
    user_broadcast_caption: str = ""
    target_groups: List[str] = []
    delivery_channels: List[str] = ["tracking_only"]
    image_path: str = ""

class PromotionCampaignUpdate(BaseModel):
    name: Optional[str] = None
    package_id: Optional[int] = None
    normal_price: Optional[float] = None
    promo_price: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    bot_badge: Optional[str] = None
    bot_sales_text: Optional[str] = None
    group_caption: Optional[str] = None
    user_broadcast_caption: Optional[str] = None
    target_groups: Optional[List[str]] = None
    delivery_channels: Optional[List[str]] = None
    image_path: Optional[str] = None
    is_active: Optional[bool] = None
