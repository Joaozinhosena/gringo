from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="client", index=True)
    avatar = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    appointments = db.relationship(
        "Appointment",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    push_subscriptions = db.relationship(
        "PushSubscription",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Professional(db.Model):
    __tablename__ = "professionals"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.String(300), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    slots = db.relationship("Slot", back_populates="professional", lazy=True)
    appointments = db.relationship("Appointment", back_populates="professional", lazy=True)


class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300), nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    appointments = db.relationship("Appointment", back_populates="service", lazy=True)


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    professional_id = db.Column(
        db.Integer,
        db.ForeignKey("professionals.id"),
        nullable=False,
        index=True,
    )
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)

    start_at = db.Column(db.DateTime, nullable=False, index=True)
    end_at = db.Column(db.DateTime, nullable=False)

    status = db.Column(db.String(20), nullable=False, default="scheduled", index=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="appointments")
    professional = db.relationship("Professional", back_populates="appointments")
    service = db.relationship("Service", back_populates="appointments")
    slots = db.relationship("Slot", back_populates="appointment", lazy=True)


class Slot(db.Model):
    __tablename__ = "slots"
    __table_args__ = (
        db.UniqueConstraint(
            "professional_id",
            "start_at",
            name="uq_professional_slot_start",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    professional_id = db.Column(
        db.Integer,
        db.ForeignKey("professionals.id"),
        nullable=False,
        index=True,
    )

    start_at = db.Column(db.DateTime, nullable=False, index=True)
    end_at = db.Column(db.DateTime, nullable=False)

    active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    appointment_id = db.Column(
        db.Integer,
        db.ForeignKey("appointments.id"),
        nullable=True,
        index=True,
    )

    # Reserva temporária enquanto o cliente está escolhendo/confirmando.
    hold_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    hold_expires_at = db.Column(db.DateTime, nullable=True, index=True)

    professional = db.relationship("Professional", back_populates="slots")
    appointment = db.relationship("Appointment", back_populates="slots")


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="push_subscriptions")
