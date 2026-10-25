from datetime import datetime
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, PriceCategory, Showtime
from app.models.venue import Hall, HallSeat, Venue


class VenueNotFound(Exception):
    pass


class HallNotFound(Exception):
    pass


class EventNotFound(Exception):
    pass


class MissingPriceCategories(Exception):
    def __init__(self, categories: list[str]):
        self.categories = categories
        super().__init__(f"Missing prices for categories: {categories}")


async def create_venue(
    session: AsyncSession, *, name: str, city: str, timezone: str
) -> Venue:
    venue = Venue(name=name, city=city, timezone=timezone)
    session.add(venue)
    await session.commit()
    await session.refresh(venue)
    return venue


async def create_hall(
    session: AsyncSession, *, venue_id: UUID, name: str
) -> Hall:
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise VenueNotFound
    hall = Hall(venue_id=venue_id, name=name)
    session.add(hall)
    await session.commit()
    await session.refresh(hall)
    return hall


async def bulk_create_seats(
    session: AsyncSession,
    *,
    hall_id: UUID,
    rows: list[str],
    seats_per_row: int,
    categories: dict[str, str],
    default_category: str,
) -> int:
    hall = await session.get(Hall, hall_id)
    if hall is None:
        raise HallNotFound

    seat_rows: list[dict] = []
    for r in rows:
        cat = categories.get(r, default_category)
        for n in range(1, seats_per_row + 1):
            seat_rows.append(
                {
                    "hall_id": hall_id,
                    "row_label": r,
                    "seat_number": n,
                    "category": cat,
                }
            )

    if not seat_rows:
        return 0

    await session.execute(insert(HallSeat), seat_rows)
    await session.commit()
    return len(seat_rows)


async def create_event(
    session: AsyncSession,
    *,
    title: str,
    description: str | None,
    venue_id: UUID,
    poster_url: str | None,
) -> Event:
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise VenueNotFound
    event = Event(
        title=title,
        description=description,
        venue_id=venue_id,
        poster_url=poster_url,
        status="published",
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def create_showtime(
    session: AsyncSession,
    *,
    event_id: UUID,
    hall_id: UUID,
    starts_at: datetime,
    sales_open_at: datetime | None,
    sales_close_at: datetime | None,
    currency: str,
    prices: dict[str, int],
) -> Showtime:
    hall = await session.get(Hall, hall_id)
    if hall is None:
        raise HallNotFound
    event = await session.get(Event, event_id)
    if event is None:
        raise EventNotFound

    # Ensure every category present in the hall has a price
    present_cats = (
        (
            await session.execute(
                select(HallSeat.category).where(HallSeat.hall_id == hall_id).distinct()
            )
        )
        .scalars()
        .all()
    )
    missing = [c for c in present_cats if c not in prices]
    if missing:
        raise MissingPriceCategories(missing)

    showtime = Showtime(
        event_id=event_id,
        hall_id=hall_id,
        starts_at=starts_at,
        sales_open_at=sales_open_at,
        sales_close_at=sales_close_at,
        status="on_sale",
        currency=currency,
    )
    session.add(showtime)
    await session.flush()

    for cat, price in prices.items():
        session.add(
            PriceCategory(showtime_id=showtime.id, category=cat, price_cents=price)
        )

    await session.execute(
        text(
            """
            INSERT INTO showtime_seats (showtime_id, hall_seat_id, price_cents, status)
            SELECT :showtime_id, hs.id, pc.price_cents, 'available'
            FROM hall_seats hs
            JOIN price_categories pc
              ON pc.showtime_id = :showtime_id AND pc.category = hs.category
            WHERE hs.hall_id = :hall_id
            """
        ),
        {"showtime_id": showtime.id, "hall_id": hall_id},
    )

    await session.commit()
    await session.refresh(showtime)
    return showtime


async def get_showtime_with_counts(
    session: AsyncSession, showtime_id: UUID
) -> tuple[Showtime, int, int] | None:
    showtime = await session.get(Showtime, showtime_id)
    if showtime is None:
        return None

    total = (
        await session.execute(
            text("SELECT count(*) FROM showtime_seats WHERE showtime_id = :sid"),
            {"sid": showtime_id},
        )
    ).scalar_one()

    available = (
        await session.execute(
            text(
                """
                SELECT count(*) FROM showtime_seats
                WHERE showtime_id = :sid
                  AND (status = 'available'
                       OR (status = 'held' AND hold_expires_at < now()))
                """
            ),
            {"sid": showtime_id},
        )
    ).scalar_one()

    return showtime, int(available), int(total)