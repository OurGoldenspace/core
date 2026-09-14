from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings, settings
from src.database import AsyncSessionLocal, Database, init_db

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


async def seed_if_empty(session: AsyncSession, app_settings: Settings | None = None) -> dict:
    app_settings = app_settings or get_settings()
    db = Database(session)
    existing = await db.get_tenant_by_api_key(app_settings.API_KEY)
    if existing is not None:
        return existing
    return await seed(session, app_settings)


async def seed(session: AsyncSession, app_settings: Settings | None = None) -> dict:
    app_settings = app_settings or get_settings()
    vendors_path = DATA_DIR / "vendors.json"
    departments_path = DATA_DIR / "departments.json"
    if not vendors_path.exists() or not departments_path.exists():
        raise FileNotFoundError(
            "Missing data/vendors.json or data/departments.json. Run python data/generate_data.py first."
        )

    vendors = json.loads(vendors_path.read_text(encoding="utf-8"))
    departments = json.loads(departments_path.read_text(encoding="utf-8"))

    result = await session.execute(
        text(
            """
            INSERT INTO tenants (name, api_key)
            VALUES (:name, :api_key)
            RETURNING id, name
            """
        ),
        {"name": app_settings.TENANT_NAME, "api_key": app_settings.API_KEY},
    )
    tenant_row = result.fetchone()
    tenant_id = tenant_row[0]

    for item in vendors:
        await session.execute(
            text(
                """
                INSERT INTO vendors (
                    tenant_id, vendor_id, name, is_approved, risk_level,
                    credit_limit, ytd_spent, country
                )
                VALUES (
                    :tenant_id, :vendor_id, :name, :is_approved, :risk_level,
                    :credit_limit, :ytd_spent, :country
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "vendor_id": item["vendor_id"],
                "name": item["name"],
                "is_approved": 1 if item["is_approved"] else 0,
                "risk_level": item.get("risk_level"),
                "credit_limit": item.get("credit_limit"),
                "ytd_spent": item.get("ytd_spent", 0),
                "country": item.get("country"),
            },
        )

    for item in departments:
        available = item.get("budget_available")
        if available is None:
            available = item["budget_annual"] - item.get("budget_spent", 0)
        await session.execute(
            text(
                """
                INSERT INTO departments (
                    tenant_id, dept_id, name, budget_annual, budget_spent,
                    budget_available, approval_threshold
                )
                VALUES (
                    :tenant_id, :dept_id, :name, :budget_annual, :budget_spent,
                    :budget_available, :approval_threshold
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "dept_id": item["dept_id"],
                "name": item["name"],
                "budget_annual": item["budget_annual"],
                "budget_spent": item.get("budget_spent", 0),
                "budget_available": available,
                "approval_threshold": item.get("approval_threshold"),
            },
        )

    await session.commit()
    return {"id": tenant_id, "name": app_settings.TENANT_NAME}


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        tenant = await seed_if_empty(session, settings)
        print(f"Seed complete: tenant_id={tenant['id']} api_key={settings.API_KEY}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
