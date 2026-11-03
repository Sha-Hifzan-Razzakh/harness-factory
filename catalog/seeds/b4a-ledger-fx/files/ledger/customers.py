"""Customers (ledger/data/customers.csv)."""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "customers.csv"


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    region: str
    credit_limit: int


@lru_cache(maxsize=1)
def all_customers() -> dict[str, Customer]:
    with DATA.open(newline="") as f:
        return {r["id"]: Customer(r["id"], r["name"], r["region"], int(r["credit_limit"])) for r in csv.DictReader(f)}


def get(customer_id: str) -> Customer:
    try:
        return all_customers()[customer_id]
    except KeyError:
        raise KeyError(f"unknown customer {customer_id}") from None


def search(text: str) -> list[Customer]:
    text = text.lower()
    return [c for c in all_customers().values() if text in c.name.lower()]
