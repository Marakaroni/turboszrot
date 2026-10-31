from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

# Importing the Flask app runs the existing schema creation and SQLite migrations.
from app.app import app as flask_app  # noqa: E402
from app.models import Order, Part, Review, User  # noqa: E402
from app.security import hash_password, encrypt_totp_secret  # noqa: E402


DEMO_PASSWORD = "TurboDemo1!"
DEMO_TOTP_SECRET = "XL6PDG5WH4CYTFJF5QGUWPYDTGZAMOBR"

USERS = (
    ("demo_sprzedawca", "Sprzedawca z opiniami"),
    ("demo_sprzedawca_czesci", "Drugi sprzedawca"),
    ("demo_sprzedawca_moto", "Trzeci sprzedawca"),
    ("demo_kupujacy", "Kupujacy z 4 zrealizowanymi zamowieniami"),
    ("demo_kupujacy_opinie", "Kupujacy z gotowymi opiniami"),
)

PARTS = (
    {
        "username": "demo_sprzedawca",
        "project_name": "Alternator BMW E46",
        "category": "Elektryka",
        "brand": "BMW",
        "condition": "uzywany",
        "model": "E46",
        "year": 2004,
        "price": 220.0,
        "quantity": 3,
        "negotiable": True,
        "description": "Sprawny alternator po demontazu. Oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca",
        "project_name": "Lampa przednia Audi A4 B8",
        "category": "Oswietlenie",
        "brand": "Audi",
        "condition": "uzywany",
        "model": "A4 B8",
        "year": 2011,
        "price": 460.0,
        "quantity": 2,
        "negotiable": False,
        "description": "Lewa lampa przednia. Oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca_czesci",
        "project_name": "Rozrusznik Opel Astra J",
        "category": "Elektryka",
        "brand": "Opel",
        "condition": "uzywany",
        "model": "Astra J",
        "year": 2013,
        "price": 180.0,
        "quantity": 4,
        "negotiable": True,
        "description": "Rozrusznik sprawdzony przed wystawieniem.",
    },
    {
        "username": "demo_sprzedawca_moto",
        "project_name": "Lusterko prawe Ford Focus MK3",
        "category": "Karoseria",
        "brand": "Ford",
        "condition": "uzywany",
        "model": "Focus MK3",
        "year": 2014,
        "price": 135.0,
        "quantity": 2,
        "negotiable": False,
        "description": "Elektryczne lusterko prawe. Oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca",
        "project_name": "DEMO KUPIONE - Skrzynia biegow VW Golf VI",
        "category": "Skrzynia biegow",
        "brand": "Volkswagen",
        "condition": "uzywany",
        "model": "Golf VI",
        "year": 2010,
        "price": 1450.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca",
        "project_name": "DEMO KUPIONE - Zacisk hamulcowy BMW E90",
        "category": "Hamulce",
        "brand": "BMW",
        "condition": "uzywany",
        "model": "E90",
        "year": 2008,
        "price": 165.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca_czesci",
        "project_name": "DEMO KUPIONE - Kompresor klimatyzacji Volvo V50",
        "category": "Klimatyzacja",
        "brand": "Volvo",
        "condition": "uzywany",
        "model": "V50",
        "year": 2009,
        "price": 390.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca_moto",
        "project_name": "DEMO KUPIONE - Pompa ABS Ford Mondeo MK4",
        "category": "Hamulce",
        "brand": "Ford",
        "condition": "uzywany",
        "model": "Mondeo MK4",
        "year": 2012,
        "price": 520.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca",
        "project_name": "DEMO KUPIONE - Sterownik silnika Audi A3",
        "category": "Elektronika",
        "brand": "Audi",
        "condition": "uzywany",
        "model": "A3",
        "year": 2007,
        "price": 310.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
    {
        "username": "demo_sprzedawca",
        "project_name": "DEMO KUPIONE - Belka tylna Peugeot 308",
        "category": "Zawieszenie",
        "brand": "Peugeot",
        "condition": "uzywany",
        "model": "308",
        "year": 2011,
        "price": 680.0,
        "quantity": 0,
        "negotiable": False,
        "description": "Kupiona oferta demonstracyjna.",
    },
)

ORDERS = (
    ("demo_kupujacy", "DEMO KUPIONE - Skrzynia biegow VW Golf VI", "odbior osobisty"),
    ("demo_kupujacy", "DEMO KUPIONE - Zacisk hamulcowy BMW E90", "dostawa"),
    ("demo_kupujacy", "DEMO KUPIONE - Kompresor klimatyzacji Volvo V50", "dostawa"),
    ("demo_kupujacy", "DEMO KUPIONE - Pompa ABS Ford Mondeo MK4", "odbior osobisty"),
    ("demo_kupujacy_opinie", "DEMO KUPIONE - Sterownik silnika Audi A3", "dostawa"),
    ("demo_kupujacy_opinie", "DEMO KUPIONE - Belka tylna Peugeot 308", "odbior osobisty"),
)

REVIEWS = (
    (
        "demo_kupujacy",
        "DEMO KUPIONE - Skrzynia biegow VW Golf VI",
        5,
        "Szybka realizacja i zgodnosc z opisem.",
    ),
    (
        "demo_kupujacy_opinie",
        "DEMO KUPIONE - Sterownik silnika Audi A3",
        4,
        "Dobry kontakt ze sprzedawca, czesc dziala prawidlowo.",
    ),
    (
        "demo_kupujacy_opinie",
        "DEMO KUPIONE - Belka tylna Peugeot 308",
        5,
        "Wszystko zgodne z ustaleniami. Polecam.",
    ),
)


def upsert_user(session: Session, username: str) -> User:
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, password_hash="", role="user")
        session.add(user)

    user.password_hash = hash_password(DEMO_PASSWORD)
    user.role = "user"
    user.totp_secret = encrypt_totp_secret(DEMO_TOTP_SECRET)
    user.totp_confirmed = True

    session.flush()
    return user


def upsert_part(session: Session, users: dict[str, User], values: dict[str, object]) -> Part:
    username = str(values["username"])
    project_name = str(values["project_name"])
    part = session.scalar(
        select(Part).where(
            Part.username == username,
            Part.project_name == project_name,
        )
    )
    if part is None:
        part = Part(
            username=username,
            project_name=project_name,
            category="",
            brand="",
            condition="",
            model="",
            year=2000,
            price=0,
            quantity=0,
        )
        session.add(part)

    part.user_id = users[username].id
    part.category = str(values["category"])
    part.brand = str(values["brand"])
    part.condition = str(values["condition"])
    part.model = str(values["model"])
    part.year = int(values["year"])
    part.price = float(values["price"])
    part.quantity = int(values["quantity"])
    part.negotiable = bool(values["negotiable"])
    part.description = str(values["description"])
    part.status = "aktywna" if part.quantity > 0 else "nieaktywny"
    part.is_active = part.quantity > 0
    session.flush()
    return part


def upsert_order(
    session: Session,
    buyer: User,
    seller: User,
    part: Part,
    delivery_option: str,
    created_at: float,
) -> Order:
    order = session.scalar(
        select(Order).where(
            Order.buyer_id == buyer.id,
            Order.part_id == part.id,
        )
    )
    if order is None:
        order = Order(buyer_id=buyer.id, part_id=part.id, created_at=created_at)
        session.add(order)

    order.seller_id = seller.id
    order.quantity = 1
    order.delivery_option = delivery_option
    order.address = "00-001 Warszawa, Testowa 1" if delivery_option == "dostawa" else None
    order.status = "zrealizowane"
    order.total_price = part.price + (15 if delivery_option == "dostawa" else 0)
    order.seller_unread = False
    session.flush()
    return order


def upsert_review(
    session: Session,
    reviewer: User,
    seller: User,
    order: Order,
    rating: int,
    comment: str,
    created_at: float,
) -> None:
    review = session.scalar(select(Review).where(Review.order_id == order.id))
    if review is None:
        review = Review(
            order_id=order.id,
            reviewer_id=reviewer.id,
            seller_id=seller.id,
            rating=rating,
            comment=comment,
            created_at=created_at,
        )
        session.add(review)
        return

    review.reviewer_id = reviewer.id
    review.seller_id = seller.id
    review.rating = rating
    review.comment = comment
    review.created_at = created_at


def seed_demo_data() -> None:
    engine = create_engine(flask_app.config["DATABASE_URL"], future=True)
    now = time.time()

    with Session(engine) as session:
        users = {username: upsert_user(session, username) for username, _ in USERS}
        parts = {
            str(values["project_name"]): upsert_part(session, users, values)
            for values in PARTS
        }

        orders: dict[tuple[str, str], Order] = {}
        for index, (buyer_username, project_name, delivery_option) in enumerate(ORDERS):
            part = parts[project_name]
            seller = users[part.username]
            orders[(buyer_username, project_name)] = upsert_order(
                session,
                users[buyer_username],
                seller,
                part,
                delivery_option,
                now - ((index + 1) * 3600),
            )

        for index, (reviewer_username, project_name, rating, comment) in enumerate(REVIEWS):
            order = orders[(reviewer_username, project_name)]
            upsert_review(
                session,
                users[reviewer_username],
                users[parts[project_name].username],
                order,
                rating,
                comment,
                now - ((index + 1) * 1800),
            )

        session.commit()

    print("Dane demonstracyjne zostaly zapisane.")
    print("Loginy i hasla: demo_logins.txt")
    print("Zaloguj sie jako demo_kupujacy i otworz /orders.")


if __name__ == "__main__":
    seed_demo_data()
