"""
Database models for the Zim Golf Deal Scraper.
SQLite by default — swap DATABASE_URL in .env to use PostgreSQL in production.
"""

import hashlib
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, Float,
    DateTime, Boolean, Text, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker
from loguru import logger

Base = declarative_base()


class Listing(Base):
    __tablename__ = "listings"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    url_hash    = Column(String(64), unique=True, nullable=False, index=True)
    url         = Column(Text, nullable=False)
    platform    = Column(String(50), nullable=False)

    # Core item fields
    title       = Column(Text)
    price_zar   = Column(Float)
    condition   = Column(String(50))
    brand       = Column(String(100))
    category    = Column(String(50))   # clubs / balls / attire / bags / shoes / other
    description = Column(Text)
    location    = Column(String(150))
    image_url   = Column(Text)

    # Scoring
    deal_score  = Column(Integer, default=0)
    is_hot_deal = Column(Boolean, default=False)

    # Lifecycle
    posted_at        = Column(DateTime)
    scraped_at       = Column(DateTime, default=datetime.utcnow)
    posted_to_wa     = Column(Boolean, default=False)
    wa_posted_at     = Column(DateTime, nullable=True)
    manually_reviewed = Column(Boolean, default=False)
    active           = Column(Boolean, default=True)

    __table_args__ = (
        Index("ix_score_active", "deal_score", "active"),
        Index("ix_platform_cat", "platform", "category"),
    )

    @staticmethod
    def make_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def __repr__(self):
        return f"<Listing [{self.platform}] {self.title[:40]!r} R{self.price_zar} score={self.deal_score}>"


class BuyerInterest(Base):
    __tablename__ = "buyer_interests"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    listing_id      = Column(Integer, nullable=False, index=True)
    listing_title   = Column(Text)
    listing_url     = Column(Text)
    name            = Column(String(100), nullable=False)
    whatsapp_number = Column(String(30), nullable=False)
    message         = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow)


class PricingConfig(Base):
    __tablename__ = "pricing_config"

    id           = Column(Integer, primary_key=True, default=1)  # singleton
    usd_rate     = Column(Float,   default=18.50)   # ZAR per 1 USD
    markup_pct   = Column(Float,   default=35.0)    # applied to landed cost
    ship_clubs   = Column(Float,   default=15.0)    # USD per item by category
    ship_bags    = Column(Float,   default=20.0)
    ship_balls   = Column(Float,   default=5.0)
    ship_attire  = Column(Float,   default=3.0)
    ship_shoes   = Column(Float,   default=5.0)
    ship_other   = Column(Float,   default=8.0)
    other_costs  = Column(Float,   default=2.0)     # customs / handling per item
    updated_at   = Column(DateTime, default=datetime.utcnow)


def init_db(db_url: str = "sqlite:///data/golf_deals.db"):
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    logger.info(f"Database ready at {db_url}")
    return engine, Session
