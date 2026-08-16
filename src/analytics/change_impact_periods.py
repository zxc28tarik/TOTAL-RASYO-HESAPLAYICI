"""
Change-impact — KANONIK DONEM ORDINALI.

Ceyrek ilerlemesi TAKVIM GUNU EKLEYEREK yapilmaz. `period_end + 90 gun`
bazen bir sonraki ceyrek sonunu vermez (ceyrekler 90/91/92 gun) ve artik
yillarda kayar. Bu, change-impact penceresinin sessizce yanlis anchor
kumesi uretmesine yol acar.

Bunun yerine (yil, ceyrek) -> tam sayi ordinali kullanilir; aritmetik
ordinal uzerinde yapilir ve sonra tarihe geri cevrilir.
"""
from __future__ import annotations

from datetime import date

QUARTER_END_DAYS: dict[int, tuple[int, int]] = {
    1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31),
}


class PeriodError(ValueError):
    pass


def quarter_of(period_end: date) -> int:
    """Ceyrek numarasi. Girdi GERCEK bir ceyrek sonu olmalidir."""
    if not isinstance(period_end, date):
        raise PeriodError("period_end date olmali")
    for ceyrek, (ay, gun) in QUARTER_END_DAYS.items():
        if period_end.month == ay and period_end.day == gun:
            return ceyrek
    raise PeriodError(f"gercek ceyrek sonu degil: {period_end}")


def period_ordinal(period_end: date) -> int:
    """(yil, ceyrek) -> monoton artan tam sayi."""
    return period_end.year * 4 + (quarter_of(period_end) - 1)


def ordinal_to_period_end(ordinal: int) -> date:
    if not isinstance(ordinal, int) or isinstance(ordinal, bool):
        raise PeriodError("ordinal int olmali")
    yil, sifir_bazli = divmod(ordinal, 4)
    ay, gun = QUARTER_END_DAYS[sifir_bazli + 1]
    return date(yil, ay, gun)


def shift_quarters(period_end: date, offset: int) -> date:
    """Ceyrek ekle/cikar. Takvim gunu aritmetigi KULLANILMAZ."""
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise PeriodError("offset int olmali")
    return ordinal_to_period_end(period_ordinal(period_end) + offset)


def affected_anchor_period_ends(
    changed_period_end: date,
    *,
    affected_anchor_count: int,
    max_forward_period_offset: int,
) -> list[date]:
    """
    Degisen ceyregin ETKILEDIGI anchor donemleri.

    OFF-BY-ONE SINIRI: TTM_4Q'da degisen ceyrek Q, yalniz Q, Q+1, Q+2, Q+3
    anchor'larinin TTM penceresine girer. Yani 4 ANCHOR ve maksimum ileri
    offset 3'tur -- 4 DEGIL. `[Q, Q+4]` yazmak 5 anchor uretir ve gereksiz
    bir donem daha yeniden hesaplatir.

    SERIES_8Q icin: 8 anchor, maksimum offset 7.
    LATEST_ONLY icin: 1 anchor, offset 0.
    """
    if not isinstance(affected_anchor_count, int) or isinstance(affected_anchor_count, bool):
        raise PeriodError("affected_anchor_count int olmali")
    if affected_anchor_count < 1:
        raise PeriodError("affected_anchor_count en az 1 olmali")
    if not isinstance(max_forward_period_offset, int) or isinstance(max_forward_period_offset, bool):
        raise PeriodError("max_forward_period_offset int olmali")
    if max_forward_period_offset < 0:
        raise PeriodError("max_forward_period_offset negatif olamaz")
    # Sayilar birbirini DOGRULAR: n anchor, 0..n-1 offset demektir.
    if max_forward_period_offset != affected_anchor_count - 1:
        raise PeriodError(
            "max_forward_period_offset, affected_anchor_count - 1 olmali "
            f"({max_forward_period_offset} != {affected_anchor_count - 1})"
        )
    baslangic = period_ordinal(changed_period_end)
    return [ordinal_to_period_end(baslangic + i)
            for i in range(affected_anchor_count)]
