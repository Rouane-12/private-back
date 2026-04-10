from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, nullable=False)

    is_verified = Column(Boolean, default=False)
    first_name = Column(String, nullable=True)
    username = Column(String, unique=True, nullable=True, index=True)

    phone = Column(String, nullable=True)
    city = Column(String, nullable=True)

    business_name = Column(String, nullable=True)
    business_type = Column(String, nullable=True)
    siret = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile_image = Column(String, nullable=True)
    profile_public_id = Column(String, nullable=True)

    # ✅ RELATIONS
    photos = relationship("Photo", back_populates="owner", cascade="all, delete-orphan")
    apartments = relationship("Apartment", back_populates="owner", cascade="all, delete-orphan")
    requested_events = relationship("Event", foreign_keys="Event.requester_id", back_populates="requester")
    woman_events = relationship("Event", foreign_keys="Event.woman_id", back_populates="woman")


class OTPCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    code = Column(String, nullable=False)
    purpose = Column(String, nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    url = Column(String, nullable=False)
    public_id = Column(String, nullable=False)
    thumbnail_url = Column(String, nullable=True)

    is_premium = Column(Boolean, default=False)
    price = Column(Float, nullable=True)
    caption = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="photos")
    unlocks = relationship("PhotoUnlock", back_populates="photo", cascade="all, delete-orphan")


class PhotoUnlock(Base):
    __tablename__ = "photo_unlocks"

    id = Column(Integer, primary_key=True, index=True)

    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    transaction_id = Column(String, nullable=True, unique=True)
    amount = Column(Float, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    photo = relationship("Photo", back_populates="unlocks")
    user = relationship("User")


class Apartment(Base):
    __tablename__ = "apartments"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    city = Column(String(100), nullable=False)
    price = Column(Float, nullable=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="apartments")
    photos = relationship("ApartmentPhoto", back_populates="apartment", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="apartment", cascade="all, delete-orphan")



class ApartmentPhoto(Base):
    __tablename__ = "apartment_photos"

    id = Column(Integer, primary_key=True, index=True)
    apartment_id = Column(Integer, ForeignKey("apartments.id"), nullable=False)

    url = Column(String, nullable=False)
    public_id = Column(String, nullable=False)

    is_cover = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    apartment = relationship("Apartment", back_populates="photos")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)

    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    woman_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    apartment_id = Column(Integer, ForeignKey("apartments.id"), nullable=False)

    title = Column(String(150), nullable=False)
    date = Column(DateTime, nullable=False)

    is_active = Column(Boolean, default=True)

    status = Column(String(30), default="pending")
    requester_status = Column(String(20), default="pending")
    woman_status = Column(String(20), default="pending")
    owner_status = Column(String(20), default="pending")
    # description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ✅ RELATIONS FIX
    requester = relationship("User", foreign_keys=[requester_id], back_populates="requested_events")
    woman = relationship("User", foreign_keys=[woman_id], back_populates="woman_events")
    apartment = relationship("Apartment", back_populates="events")

    reservations = relationship("Reservation", back_populates="event", cascade="all, delete-orphan")


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="pending")
    transaction_id = Column(String, nullable=True, unique=True)
    amount = Column(Float, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    event = relationship("Event", back_populates="reservations")
    user = relationship("User")


class Story(Base):
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, index=True)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    url = Column(String, nullable=False)
    public_id = Column(String, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)

    owner = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    message = Column(String(255), nullable=False)
    type = Column(String(50), default="event_request")
    is_read = Column(Boolean, default=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
    event = relationship("Event")