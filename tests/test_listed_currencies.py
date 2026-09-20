from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.market_service import (
    LISTED_CURRENCIES_PAGE_SIZE,
    get_listed_currencies_page,
)


class FakeScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalarRows(self._rows)


class FakeSession:
    def __init__(self, total_count: int, rows: list[str]):
        self.total_count = total_count
        self.rows = rows
        self.count_statement = None
        self.page_statement = None

    async def scalar(self, statement):
        self.count_statement = statement
        return self.total_count

    async def execute(self, statement):
        self.page_statement = statement
        return FakeResult(self.rows)


@pytest.mark.asyncio
async def test_listed_currency_page_is_capped_at_100_and_sorted_by_price_query():
    session = FakeSession(
        total_count=250,
        rows=[f"C{i:03d}" for i in range(LISTED_CURRENCIES_PAGE_SIZE)],
    )

    page = await get_listed_currencies_page(session, page=1)

    assert page.page == 1
    assert page.total_pages == 3
    assert page.total_count == 250
    assert len(page.currency_codes) == 100
    assert page.currency_codes[0] == "C000"

    compiled = str(
        session.page_statement.compile(compile_kwargs={"literal_binds": True})
    )
    assert "ORDER BY nations.exchange_rate ASC, nations.nation_id ASC" in compiled
    assert session.page_statement._limit_clause.value == LISTED_CURRENCIES_PAGE_SIZE
    assert session.page_statement._offset_clause.value == LISTED_CURRENCIES_PAGE_SIZE


@pytest.mark.asyncio
async def test_listed_currency_page_clamps_out_of_range_page():
    session = FakeSession(total_count=201, rows=["LAST"])

    page = await get_listed_currencies_page(session, page=999)

    assert page.page == 2
    assert page.total_pages == 3
    assert page.currency_codes == ("LAST",)
    assert session.page_statement._offset_clause.value == 200
