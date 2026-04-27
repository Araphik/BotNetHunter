from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    requests_limit = Column(Integer, default=100)
    requests_today = Column(Integer, default=0)
    last_request_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    history = relationship("AnalysisHistory", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<User(email='{self.email}', is_admin={self.is_admin})>"


class AnalysisHistory(Base):
    __tablename__ = "analysis_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    target = Column(String(255), nullable=False)
    target_type = Column(String(10), default="user")
    
    score = Column(Integer, nullable=True)
    risk_level = Column(String(20), nullable=True)
    details = Column(Text, nullable=True)
    
    average_score = Column(Float, nullable=True)
    score_distribution = Column(Text, nullable=True)
    members_analyzed = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="completed")
    
    user = relationship("User", back_populates="history")

    def __repr__(self):
        return f"<AnalysisHistory(id={self.id}, target='{self.target}', type='{self.target_type}')>"


class AdminSettings(Base):
    __tablename__ = "admin_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AdminSettings(key='{self.key}')>"


class SystemMetrics(Base):
    __tablename__ = "system_metrics"
    id = Column(Integer, primary_key=True)
    metric_name = Column(String(100), nullable=False, index=True)
    metric_value = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<SystemMetrics(metric='{self.metric_name}', value={self.metric_value})>"


class ModuleParameter(Base):
    __tablename__ = "module_parameters"
    
    id = Column(Integer, primary_key=True)
    module_name = Column(String(100), nullable=False)
    param_key = Column(String(100), nullable=False)
    param_value = Column(Integer, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = ({"sqlite_autoincrement": True},)

    def __repr__(self):
        return f"<ModuleParameter(module='{self.module_name}', key='{self.param_key}', value={self.param_value})>"


class VKToken(Base):
    """Модель для хранения VK API токенов"""
    __tablename__ = "vk_tokens"
    
    id = Column(Integer, primary_key=True)
    token = Column(String(500), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    requests_count = Column(Integer, default=0)
    
    def __repr__(self):
        return f"<VKToken(id={self.id}, active={self.is_active})>"