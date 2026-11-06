from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = "1.0"
DICTIONARY_VERSION = "1.0"


class Base(DeclarativeBase):
    pass


class DatasetMeta(Base):
    __tablename__ = "dataset_meta"

    dataset_version: Mapped[str] = mapped_column(Text, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zip_checksum: Mapped[str | None] = mapped_column(String(64))
    file_checksums: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    row_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    min_deal_date: Mapped[date | None] = mapped_column(Date)
    max_deal_date: Mapped[date | None] = mapped_column(Date)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    dictionary_version: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("validation_status IN ('PASSED','FAILED')", name="ck_dataset_meta_status"),
        Index(
            "ux_dataset_meta_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class Region(Base):
    __tablename__ = "regions"

    region_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    canonical_name_ru: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name_uz: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class RegionAlias(Base):
    __tablename__ = "region_aliases"

    alias_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("regions.region_id", ondelete="RESTRICT"), nullable=False
    )
    alias_raw: Mapped[str] = mapped_column(Text, nullable=False)
    alias_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "language IN ('ru','uz_latn','uz_cyrl')", name="ck_region_alias_language"
        ),
        UniqueConstraint("alias_normalized", "region_id", name="ux_region_alias_region"),
        Index("ix_region_aliases_normalized", "alias_normalized"),
    )


class Participant(Base):
    __tablename__ = "participants"

    inn: Mapped[str] = mapped_column(String(9), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    company_region_raw: Mapped[str] = mapped_column(Text, nullable=False)
    company_region_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("regions.region_id", ondelete="RESTRICT")
    )
    activity_type: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    vat_payer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    vat_payer_raw: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("inn ~ '^[0-9]{9}$'", name="ck_participants_inn"),
        CheckConstraint("role IN ('Продавец','Покупатель')", name="ck_participants_role"),
        Index("ix_participants_name_normalized", "name_normalized"),
        Index("ix_participants_company_region", "company_region_id"),
        Index("ix_participants_role", "role"),
        Index(
            "ix_participants_name_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )


class Broker(Base):
    __tablename__ = "brokers"

    broker_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    inn: Mapped[str] = mapped_column(String(9), nullable=False)
    region_raw: Mapped[str] = mapped_column(Text, nullable=False)
    region_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("regions.region_id", ondelete="RESTRICT")
    )
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("inn ~ '^[0-9]{9}$'", name="ck_brokers_inn"),
        Index("ix_brokers_name_normalized", "name_normalized"),
        Index("ix_brokers_region", "region_id"),
        Index(
            "ix_brokers_name_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )


class Contract(Base):
    __tablename__ = "contracts"

    contract_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    product_name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    product_size: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str | None] = mapped_column(Text)
    product_class: Mapped[str | None] = mapped_column(Text)
    hs_code: Mapped[str] = mapped_column(String(12), nullable=False)
    product_group: Mapped[str] = mapped_column(Text, nullable=False)
    product_category: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(Text, nullable=False)
    unit_name: Mapped[str] = mapped_column(Text, nullable=False)
    lot_multiplier: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    pledge_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    delivery_basis: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse_region_raw: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse_region_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("regions.region_id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_address: Mapped[str] = mapped_column(Text, nullable=False)
    trade_type: Mapped[str] = mapped_column(Text, nullable=False)
    contract_type: Mapped[str] = mapped_column(Text, nullable=False)
    contract_form: Mapped[str] = mapped_column(Text, nullable=False)
    payment_days: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, nullable=False)
    packaging: Mapped[str] = mapped_column(Text, nullable=False)
    seller_inn: Mapped[str] = mapped_column(
        String(9), ForeignKey("participants.inn", ondelete="RESTRICT"), nullable=False
    )
    registration_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("lot_multiplier > 0", name="ck_contracts_lot"),
        CheckConstraint(
            "currency IN ('Узбекский сум','Доллар США')", name="ck_contracts_currency"
        ),
        CheckConstraint(
            "contract_type IN ('Внутренний','Экспортный')", name="ck_contracts_type"
        ),
        CheckConstraint(
            "contract_form IN ('Спот','Форвард','Фьючерс')", name="ck_contracts_form"
        ),
        CheckConstraint("base_price >= 0", name="ck_contracts_base_price"),
        CheckConstraint("pledge_percent >= 0", name="ck_contracts_pledge"),
        CheckConstraint("payment_days >= 0", name="ck_contracts_payment_days"),
        CheckConstraint("delivery_days >= 0", name="ck_contracts_delivery_days"),
        CheckConstraint("valid_until >= registration_date", name="ck_contracts_validity"),
        Index("ix_contracts_seller_inn", "seller_inn"),
        Index("ix_contracts_hs_code", "hs_code"),
        Index("ix_contracts_product_category", "product_category"),
        Index("ix_contracts_product_name_norm", "product_name_normalized"),
        Index("ix_contracts_contract_type", "contract_type"),
        Index("ix_contracts_currency", "currency"),
        Index(
            "ix_contracts_product_name_trgm",
            "product_name_normalized",
            postgresql_using="gin",
            postgresql_ops={"product_name_normalized": "gin_trgm_ops"},
        ),
    )


class Deal(Base):
    __tablename__ = "deals"

    deal_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    deal_date: Mapped[date] = mapped_column(Date, nullable=False)
    deal_time: Mapped[time] = mapped_column(Time, nullable=False)
    trade_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_lots: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deal_amount: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    starting_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    contract_no: Mapped[str] = mapped_column(
        String(20), ForeignKey("contracts.contract_no", ondelete="RESTRICT"), nullable=False
    )
    deal_product_name: Mapped[str] = mapped_column(Text, nullable=False)
    deal_product_name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    lot_multiplier: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_name: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse_region_raw: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse_region_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("regions.region_id", ondelete="RESTRICT"), nullable=False
    )
    contract_type: Mapped[str] = mapped_column(Text, nullable=False)
    contract_form: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    seller_inn: Mapped[str] = mapped_column(
        String(9), ForeignKey("participants.inn", ondelete="RESTRICT"), nullable=False
    )
    buyer_inn: Mapped[str] = mapped_column(
        String(9), ForeignKey("participants.inn", ondelete="RESTRICT"), nullable=False
    )
    seller_broker_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("brokers.broker_code", ondelete="RESTRICT"), nullable=False
    )
    buyer_broker_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("brokers.broker_code", ondelete="RESTRICT"), nullable=False
    )
    payment_date: Mapped[date | None] = mapped_column(Date)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    delivered_lots: Mapped[int | None] = mapped_column(BigInteger)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_row: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity_lots > 0", name="ck_deals_quantity"),
        CheckConstraint("lot_multiplier > 0", name="ck_deals_lot"),
        CheckConstraint("deal_amount >= 0", name="ck_deals_amount"),
        CheckConstraint("starting_price >= 0", name="ck_deals_starting_price"),
        CheckConstraint(
            "delivered_lots IS NULL OR (delivered_lots >= 0 AND delivered_lots <= quantity_lots)",
            name="ck_deals_delivered",
        ),
        CheckConstraint(
            "contract_type IN ('Внутренний','Экспортный')", name="ck_deals_type"
        ),
        CheckConstraint(
            "contract_form IN ('Спот','Форвард','Фьючерс')", name="ck_deals_form"
        ),
        Index("ix_deals_deal_date", "deal_date"),
        Index("ix_deals_contract_no", "contract_no"),
        Index("ix_deals_seller_inn", "seller_inn"),
        Index("ix_deals_buyer_inn", "buyer_inn"),
        Index("ix_deals_seller_broker_code", "seller_broker_code"),
        Index("ix_deals_buyer_broker_code", "buyer_broker_code"),
        Index("ix_deals_status", "status"),
        Index("ix_deals_contract_type", "contract_type"),
        Index("ix_deals_warehouse_region", "warehouse_region_id"),
        Index("ix_deals_unit_name", "unit_name"),
        Index(
            "ix_deals_product_name_trgm",
            "deal_product_name_normalized",
            postgresql_using="gin",
            postgresql_ops={"deal_product_name_normalized": "gin_trgm_ops"},
        ),
    )


class UsdRate(Base):
    __tablename__ = "usd_rates"

    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    uzs_per_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (CheckConstraint("uzs_per_usd > 0", name="ck_usd_rates_positive"),)


class KnowledgeChunkRecord(Base):
    """Approved reference-material chunk.

    This table is knowledge, not transaction data.  It must never contain deal
    rows, query results or model answers.  Retrieval scores are interpretation
    hints only and never evidence of a fact.
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    script: Mapped[str | None] = mapped_column(Text)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    approval_status: Mapped[str] = mapped_column(Text, nullable=False)
    trust_level: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id", "content_hash", name="ux_knowledge_chunks_source_hash"
        ),
        CheckConstraint(
            "approval_status IN ('DRAFT','APPROVED','RETIRED')",
            name="ck_knowledge_chunks_approval",
        ),
        CheckConstraint(
            "trust_level IN ('AUTHORITATIVE','CURATED','EXAMPLE')",
            name="ck_knowledge_chunks_trust",
        ),
        Index("ix_knowledge_chunks_source_type", "source_type"),
        Index("ix_knowledge_chunks_approval", "approval_status"),
    )
