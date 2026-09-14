"""
Synthetic Data Generator for WorkCore Invoice Demo

Generates realistic but synthetic data:
- 20 vendors (with approval status, risk levels, credit limits)
- 5 departments (with budgets, thresholds)
- 100 sample invoices (various statuses, amounts)

This data makes the demo look professional and realistic.
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

# === DATA DEFINITIONS ===

VENDOR_NAMES = [
    "Acme Corp Supplies", "Global Tech Solutions", "Office Essentials Plus",
    "Premium Hardware Inc", "Digital Innovation Ltd", "Cloud Services Group",
    "Enterprise Software Co", "Industrial Supplies Direct", "Professional Services LLC",
    "Tech Components Ltd", "Business Solutions Inc", "Manufacturing Supplies Corp",
    "Consulting Partners Global", "Security Systems Inc", "Logistics Plus",
    "Marketing Services Co", "IT Support Solutions", "Maintenance Services Group",
    "Procurement Direct", "Vendor Management Inc"
]

DEPARTMENTS = [
    {"name": "Engineering", "budget": 100000, "threshold": 5000},
    {"name": "Finance", "budget": 50000, "threshold": 3000},
    {"name": "HR", "budget": 30000, "threshold": 2000},
    {"name": "Operations", "budget": 75000, "threshold": 4000},
    {"name": "Sales", "budget": 60000, "threshold": 5000}
]

RISK_LEVELS = ["low", "medium", "high"]
COUNTRIES = ["USA", "Canada", "UK", "Germany", "France"]

# === GENERATION FUNCTIONS ===

def generate_vendors(count=20):
    """Generate realistic vendors with approval status and risk levels."""
    vendors = []
    for i in range(1, count + 1):
        vendor = {
            "vendor_id": i,
            "name": VENDOR_NAMES[i - 1],
            "is_approved": random.choice([True, True, True, False]),  # 75% approved
            "risk_level": random.choice(RISK_LEVELS),
            "credit_limit": random.choice([25000, 50000, 100000, 150000]),
            "ytd_spent": random.randint(0, 50000),
            "country": random.choice(COUNTRIES),
        }
        vendors.append(vendor)

    # README curl and interview demo depend on this row.
    vendors[0].update(
        {
            "name": "Acme Corp Supplies",
            "is_approved": True,
            "risk_level": "low",
            "credit_limit": 50000,
            "ytd_spent": 12500,
            "country": "USA",
        }
    )
    vendors[1].update(
        {
            "name": "Global Tech Solutions",
            "is_approved": True,
            "risk_level": "high",
        }
    )
    vendors[-1].update(
        {
            "name": "Vendor Management Inc",
            "is_approved": False,
            "risk_level": "high",
        }
    )
    return vendors


def generate_departments():
    """Generate departments with budgets and approval thresholds."""
    departments = []
    for i, dept in enumerate(DEPARTMENTS, 1):
        department = {
            "dept_id": i,
            "name": dept["name"],
            "budget_annual": dept["budget"],
            "budget_spent": random.randint(0, int(dept["budget"] * 0.5)),
            "approval_threshold": dept["threshold"],
        }
        # Calculate remaining budget
        department["budget_available"] = (
            department["budget_annual"] - department["budget_spent"]
        )
        departments.append(department)
    return departments


def generate_invoices(vendors, departments, count=100):
    """Generate realistic invoice samples."""
    invoices = []
    base_date = datetime.now() - timedelta(days=30)

    for i in range(1, count + 1):
        vendor = random.choice(vendors)
        department = random.choice(departments)

        # Generate realistic amount
        if random.random() < 0.7:
            amount = random.randint(100, 4999)  # 70% under threshold
        else:
            amount = random.randint(5000, 25000)  # 30% over threshold

        # Generate invoice date (within last 30 days)
        invoice_date = base_date + timedelta(days=random.randint(0, 30))

        invoice = {
            "invoice_id": f"INV-2024-{i:05d}",
            "vendor_id": vendor["vendor_id"],
            "vendor_name": vendor["name"],
            "department_id": department["dept_id"],
            "department_name": department["name"],
            "amount": float(amount),
            "date": invoice_date.strftime("%Y-%m-%d"),
            "status": "pending",  # All invoices start as pending
            "description": f"Invoice for {vendor['name']} services",
        }
        invoices.append(invoice)

    invoices[0] = {
        "invoice_id": "INV-2024-00001",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "department_name": departments[0]["name"],
        "amount": 2500.00,
        "date": "2024-09-13",
        "status": "pending",
        "description": "Office supplies",
    }
    return invoices


# === MAIN ===

def main():
    """Generate all data and save to JSON files."""
    random.seed(42)
    print("Generating synthetic data...")

    vendors = generate_vendors(20)
    departments = generate_departments()
    invoices = generate_invoices(vendors, departments, 100)

    # Create data directory if it doesn't exist
    data_dir = Path(__file__).parent
    data_dir.mkdir(exist_ok=True)

    # Save to files
    with open(data_dir / "vendors.json", "w") as f:
        json.dump(vendors, f, indent=2)
    print(f"Generated {len(vendors)} vendors -> vendors.json")

    with open(data_dir / "departments.json", "w") as f:
        json.dump(departments, f, indent=2)
    print(f"Generated {len(departments)} departments -> departments.json")

    with open(data_dir / "sample_invoices.json", "w") as f:
        json.dump(invoices, f, indent=2)
    print(f"Generated {len(invoices)} sample invoices -> sample_invoices.json")

    print("\nData Summary:")
    print(f"  Vendors: {len(vendors)}")
    print(f"  Departments: {len(departments)}")
    print(f"  Sample Invoices: {len(invoices)}")
    print(f"  Amount Range: ${min(inv['amount'] for inv in invoices)} - ${max(inv['amount'] for inv in invoices)}")
    print(f"  Date Range: {min(inv['date'] for inv in invoices)} - {max(inv['date'] for inv in invoices)}")


if __name__ == "__main__":
    main()