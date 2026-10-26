"""Registered helper values retain their sandbox exposure policy after conversion."""

from dataclasses import dataclass

import pytest
from pydantic_monty import AsyncMonty


@dataclass(frozen=True)
class _Item:
    text: str

    def upper(self) -> str:
        return self.text.upper()

    def _private(self) -> str:
        return "not exposed"


@dataclass(frozen=True)
class _Envelope:
    items: tuple[_Item, ...]


@pytest.fixture
def converter(tmp_path, monkeypatch):
    from core.runtime import paths

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")
    from core.authoring.runtime.monty_values import MontyValueConverter

    return MontyValueConverter((_Item, _Envelope))


@pytest.mark.asyncio
async def test_registered_values_cross_inputs_and_nested_helper_results(converter):
    envelope = _Envelope((_Item("result"),))

    async def retrieve():
        return envelope

    async with AsyncMonty() as pool:
        async with pool.checkout() as session:
            result = await session.feed_run(
                "[seed.upper(), (await retrieve()).items[0].upper()]",
                inputs=converter.convert({"seed": _Item("input")}),
                external_lookup={"retrieve": converter.wrap_function(retrieve)},
            )
            assert result == ["INPUT", "RESULT"]


@pytest.mark.asyncio
async def test_registered_values_do_not_expose_private_methods(converter):
    async with AsyncMonty() as pool:
        async with pool.checkout() as session:
            with pytest.raises(Exception, match="_private"):
                await session.feed_run(
                    "seed._private()",
                    inputs=converter.convert({"seed": _Item("input")}),
                )
