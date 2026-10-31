from __future__ import annotations

import os
import math
import re
import sys
import time
import base64
import io
import qrcode
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, flash, redirect, render_template, request, url_for, session as flask_session
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy import create_engine, delete, select, text, func
from sqlalchemy.orm import Session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from app.models import Base, Favorite, Part, User, Order, Review, Conversation, Message
from app.security import (
    clear_failed_logins,
    get_login_attempt_key,
    hash_password,
    is_login_blocked,
    password_needs_argon2_rehash,
    record_failed_login,
    validate_strong_password,
    verify_password,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
    encrypt_totp_secret,
    decrypt_totp_secret,
)


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
PHOTO_SEPARATOR = "|"
# Previously enforced account roles; now every account can buy and sell.
PART_STATUSES = ("aktywna", "nieaktywny")
PUBLIC_PART_STATUSES = ("aktywna",)
PART_CATEGORIES = (
    "Silnik i osprzęt",
    "Układ napędowy",
    "Zawieszenie i układ kierowniczy",
    "Układ hamulcowy",
    "Elektryka i elektronika",
    "Układ chłodzenia i klimatyzacji",
    "Karoseria i nadwozie",
    "Oświetlenie",
    "Wnętrze",
    "Koła i opony",
    "Układ wydechowy",
    "Inne",
)
PART_CONDITIONS = ("Nowe", "Używane")
PICKUP_OPTION = "odbiór osobisty"
DELIVERY_OPTION = "dostawa"
DELIVERY_SURCHARGE = 15.0


def create_app() -> Flask:
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "powinno-byc-w-pliku-env-to-jest-niedopuszczalne-na-produkcji-tylko-do-testow") #ZMIENIĆ NA PRODUKCJI W FINALNEJ WERSJI, wywalić drugi parametr, powinno być tylko os.environ.get("SECRET_KEY") i ustawić w .env
    app.config["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL",
        "sqlite:///dev.sqlite",
    ) # lokalny fallback; w Dockerze nadpisac przez .env na sqlite:////data/turbo_szrot.sqlite
    app.config["UPLOAD_FOLDER"] = os.environ.get(
        "UPLOAD_FOLDER",
        "app/static/uploads",
    )
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)
    ) # tak samo tutaj, powinno być tylko os.environ.get("MAX_CONTENT_LENGTH") i ustawić w .env

    trusted_hosts = os.environ.get("TRUSTED_HOSTS", "localhost,127.0.0.1")
    app.config["TRUSTED_HOSTS"] = [
        host.strip() for host in trusted_hosts.split(",") if host.strip()
    ] # tak samo tutaj, powinno być tylko os.environ.get("TRUSTED_HOSTS") i ustawić w .env

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_prefix=1,
    )

    upload_dir = Path(app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(app.config["DATABASE_URL"], future=True)
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Zaloguj sie, aby wykonac te akcje."
    login_manager.init_app(app)

    with app.app_context():
        Base.metadata.create_all(engine)
        migrate_database(engine)

    @app.context_processor
    def inject_photo_helpers():
        return {
            "part_photos": part_photos,
            "primary_part_photo": primary_part_photo,
        }

    def list_parts_for_user(user_id: int) -> list[Part]:
        with Session(engine) as session:
            query = (
                select(Part)
                .where(Part.user_id == user_id)
                .order_by(Part.id.desc())
            )
            return list(session.scalars(query))

    def list_active_parts() -> list[Part]:
        """Get offers visible in the public catalog."""
        with Session(engine) as session:
            query = (
                select(Part)
                .where(
                    Part.status.in_(PUBLIC_PART_STATUSES),
                    Part.is_active == True,
                    Part.quantity > 0,
                )
                .order_by(Part.id.desc())
            )
            return list(session.scalars(query))

    def get_seller_rating(seller_id: int | None) -> dict[str, float | int]:
        if seller_id is None:
            return {"average": 0.0, "count": 0}

        with Session(engine) as session:
            average, count = session.execute(
                select(func.avg(Review.rating), func.count(Review.id))
                .where(Review.seller_id == seller_id)
            ).one()
            return {
                "average": round(float(average or 0), 1),
                "count": int(count or 0),
            }

    def get_seller_ratings_for_parts(parts: list[Part]) -> dict[int, dict[str, float | int]]:
        seller_ids = {part.user_id for part in parts if part.user_id is not None}
        ratings = {
            seller_id: {"average": 0.0, "count": 0}
            for seller_id in seller_ids
        }
        if not seller_ids:
            return ratings

        with Session(engine) as session:
            rows = session.execute(
                select(Review.seller_id, func.avg(Review.rating), func.count(Review.id))
                .where(Review.seller_id.in_(seller_ids))
                .group_by(Review.seller_id)
            )
            for seller_id, average, count in rows:
                ratings[seller_id] = {
                    "average": round(float(average or 0), 1),
                    "count": int(count or 0),
                }
        return ratings

    def list_reviews_for_seller(seller_id: int) -> list[dict[str, object]]:
        with Session(engine) as session:
            rows = session.execute(
                select(Review, User.username)
                .join(User, User.id == Review.reviewer_id)
                .where(Review.seller_id == seller_id)
                .order_by(Review.created_at.desc())
            )
            return [
                {
                    "rating": review.rating,
                    "comment": review.comment,
                    "reviewer_username": reviewer_username,
                }
                for review, reviewer_username in rows
            ]

    def list_public_parts_for_seller(user_id: int) -> list[Part]:
        with Session(engine) as session:
            query = (
                select(Part)
                .where(
                    Part.user_id == user_id,
                    Part.status.in_(PUBLIC_PART_STATUSES),
                    Part.is_active == True,
                    Part.quantity > 0,
                )
                .order_by(Part.id.desc())
            )
            return list(session.scalars(query))

    def list_favorite_parts(user_id: int) -> list[Part]:
        with Session(engine) as session:
            query = (
                select(Part)
                .join(Favorite, Favorite.part_id == Part.id)
                .where(
                    Favorite.user_id == user_id,
                    Part.status.in_(PUBLIC_PART_STATUSES),
                    Part.is_active == True,
                    Part.quantity > 0,
                )
                .order_by(Favorite.created_at.desc())
            )
            return list(session.scalars(query))

    def get_part_by_id(part_id: int) -> Part | None:
        with Session(engine) as session:
            return session.get(Part, part_id)

    def delete_part(part_id: int) -> bool:
        with Session(engine) as session:
            part = session.get(Part, part_id)
            if part is None:
                return False
            session.execute(delete(Favorite).where(Favorite.part_id == part_id))
            session.delete(part)
            session.commit()
            return True

    def update_part(part: Part) -> None:
        with Session(engine) as session:
            session.merge(part)
            session.commit()

    def save_part(part: Part) -> None:
        with Session(engine) as session:
            session.add(part)
            session.commit()

    def create_order(buyer_id: int, part_id: int, quantity: int, delivery_option: str, address: str | None) -> int:
        with Session(engine) as session:
            part = session.get(Part, part_id)
            if part is None:
                raise ValueError("Part does not exist")

            if part.quantity < quantity:
                raise ValueError("Not enough quantity")

            normalized_delivery_option = (delivery_option or PICKUP_OPTION).strip().casefold()
            if normalized_delivery_option == PICKUP_OPTION.casefold():
                delivery_option_value = PICKUP_OPTION
                delivery_fee = 0.0
                address_value = None
            elif normalized_delivery_option == DELIVERY_OPTION.casefold():
                delivery_option_value = DELIVERY_OPTION
                delivery_fee = DELIVERY_SURCHARGE
                address_value = clean_text(address, max_len=1000)
                if not address_value:
                    raise ValueError("Podaj adres dostawy dla opcji dostawy.")
            else:
                raise ValueError("Nieprawidłowy sposob dostawy")

            negotiated_price = part.price
            latest_action = session.scalar(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.part_id == part_id,
                    Conversation.buyer_id == buyer_id,
                    Message.message_type.in_(['accept', 'reject'])
                ).order_by(Message.created_at.desc())
            )
            if latest_action and latest_action.message_type == 'accept' and latest_action.price_offer:
                negotiated_price = latest_action.price_offer

            total_price = round((negotiated_price * quantity) + delivery_fee, 2)
            order = Order(
                buyer_id=buyer_id,
                seller_id=part.user_id,
                part_id=part_id,
                quantity=quantity,
                delivery_option=delivery_option_value,
                address=address_value,
                status="nowe",
                total_price=total_price,
                created_at=time.time(),
            )

            session.add(order)
            session.flush()
            order_id = order.id

            # update part quantity and status
            part.quantity = max(0, part.quantity - quantity)
            if part.quantity == 0:
                part.status = "nieaktywny"
                part.is_active = False

            session.commit()
            return order_id

    def is_favorite(user_id: int, part_id: int) -> bool:
        with Session(engine) as session:
            query = select(Favorite).where(
                Favorite.user_id == user_id,
                Favorite.part_id == part_id,
            )
            return session.scalar(query) is not None

    def add_favorite(user_id: int, part_id: int) -> None:
        with Session(engine) as session:
            query = select(Favorite).where(
                Favorite.user_id == user_id,
                Favorite.part_id == part_id,
            )
            if session.scalar(query) is None:
                session.add(Favorite(user_id=user_id, part_id=part_id, created_at=time.time()))
                session.commit()

    def remove_favorite(user_id: int, part_id: int) -> None:
        with Session(engine) as session:
            session.execute(
                delete(Favorite).where(
                    Favorite.user_id == user_id,
                    Favorite.part_id == part_id,
                )
            )
            session.commit()

    def find_user_by_username(username: str) -> User | None:
        with Session(engine) as db_session:
            return db_session.scalar(select(User).where(User.username == username))

    def find_user_by_id(user_id: int) -> User | None:
        with Session(engine) as db_session:
            return db_session.get(User, user_id)

    def generate_qr_code_data_uri(username: str, secret: str) -> str:
        totp_uri = get_totp_uri(username, secret)

        image = qrcode.make(totp_uri)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        encoded_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded_image}"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        try:
            parsed_user_id = int(user_id)
        except ValueError:
            return None

        return find_user_by_id(parsed_user_id)

    def save_user(username: str, password: str) -> int:
        with Session(engine) as db_session:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role="user",
                totp_secret=encrypt_totp_secret(generate_totp_secret()),
                totp_confirmed=False,
            )
            db_session.add(user)
            db_session.commit()
            return user.id

    def update_user_password_hash(user_id: int, password: str) -> None:
        with Session(engine) as db_session:
            user = db_session.get(User, user_id)
            if user is None:
                return

            user.password_hash = hash_password(password)
            db_session.commit()

    @app.route("/")
    def home():
        # Show all active offers to everyone (public catalog)
        search_query = clean_text(request.args.get("q"), max_len=100).casefold()
        category_filter = clean_text(request.args.get("category"), max_len=100).casefold()
        brand_filter = clean_text(request.args.get("brand"), max_len=100).casefold()
        model_filter = clean_text(request.args.get("model"), max_len=100).casefold()
        condition_filter = clean_text(request.args.get("condition"), max_len=100).casefold()
        negotiable_filter = clean_text(request.args.get("negotiable"), max_len=20).casefold()
        min_year = parse_int(request.args.get("min_year"), minimum=1800, maximum=2100)
        max_year = parse_int(request.args.get("max_year"), minimum=1800, maximum=2100)
        min_price = parse_float(request.args.get("min_price"), minimum=0)
        max_price = parse_float(request.args.get("max_price"), minimum=0)

        parts = list_active_parts()

        def matches_search(part: Part) -> bool:
            return not search_query or (
                search_query in (part.project_name or "").casefold()
                or search_query in (part.category or "").casefold()
                or search_query in (part.brand or "").casefold()
                or search_query in (part.model or "").casefold()
                or search_query in (part.condition or "").casefold()
                or search_query in str(part.year).casefold()
                or search_query in (part.username or "").casefold()
            )

        def matches_filters(part: Part) -> bool:
            if category_filter and category_filter not in (part.category or "").casefold():
                return False
            if brand_filter and brand_filter not in (part.brand or "").casefold():
                return False
            if model_filter and model_filter not in (part.model or "").casefold():
                return False
            if condition_filter and condition_filter not in (part.condition or "").casefold():
                return False
            if negotiable_filter == "tak" and not part.negotiable:
                return False
            if negotiable_filter == "nie" and part.negotiable:
                return False
            if min_year is not None and part.year < min_year:
                return False
            if max_year is not None and part.year > max_year:
                return False
            if min_price is not None and part.price < min_price:
                return False
            if max_price is not None and part.price > max_price:
                return False
            return True

        parts = [part for part in parts if matches_search(part) and matches_filters(part)]

        return render_template(
            "home.html",
            parts=parts,
            seller_ratings=get_seller_ratings_for_parts(parts),
        )

    @app.route("/offer/<int:part_id>")
    def offer_detail(part_id: int):
        part = get_part_by_id(part_id)

        if part is None:
            flash("Oferta nie istnieje.")
            return redirect(url_for("home"))

        if part.status not in PUBLIC_PART_STATUSES:
            flash("Ta oferta nie jest dostepna.")
            return redirect(url_for("home"))

        seller = find_user_by_id(part.user_id) if part.user_id else None
        seller_rating = get_seller_rating(part.user_id)
        seller_reviews = list_reviews_for_seller(part.user_id) if part.user_id else []
        favorite = (
            is_favorite(current_user.id, part.id)
            if current_user.is_authenticated
            else False
        )

        negotiated_price = part.price
        if current_user.is_authenticated:
            with Session(engine) as session:
                latest_action = session.scalar(
                    select(Message)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.part_id == part_id,
                        Conversation.buyer_id == current_user.id,
                        Message.message_type.in_(['accept', 'reject'])
                    ).order_by(Message.created_at.desc())
                )
                if latest_action and latest_action.message_type == 'accept' and latest_action.price_offer:
                    negotiated_price = latest_action.price_offer

        return render_template(
            "offer_detail.html",
            part=part,
            seller=seller,
            favorite=favorite,
            seller_rating=seller_rating,
            seller_reviews=seller_reviews,
            negotiated_price=negotiated_price,
        )

    @app.route("/quick_buy/<int:part_id>", methods=["POST"])
    @login_required
    def quick_buy(part_id: int):
        part = get_part_by_id(part_id)

        if part is None or part.status not in PUBLIC_PART_STATUSES or part.quantity < 1:
            flash("Oferta nie jest dostepna do kupienia.")
            return redirect(url_for("offer_detail", part_id=part_id))

        # quantity optional, default 1
        quantity = parse_int(request.form.get("quantity"), minimum=1, maximum=part.quantity) or 1
        delivery_option = clean_text(request.form.get("delivery_option"), max_len=50) or PICKUP_OPTION
        if delivery_option.strip().casefold() == DELIVERY_OPTION.casefold():
            city = clean_text(request.form.get("city"), max_len=100)
            postal_code = clean_text(request.form.get("postal_code"), max_len=20)
            street = clean_text(request.form.get("street"), max_len=200)
            house_number = clean_text(request.form.get("house_number"), max_len=50)
            apartment_number = clean_text(request.form.get("apartment_number"), max_len=50)

            if not city or not postal_code or not street or not house_number:
                flash("Podaj miasto, kod pocztowy, ulice i numer domu lub mieszkania.")
                return redirect(url_for("offer_detail", part_id=part_id))

            address_line = f"{street} {house_number}"
            if apartment_number:
                address_line = f"{address_line}/{apartment_number}"
            address = f"{postal_code} {city}, {address_line}"
        else:
            address = None

        try:
            order_id = create_order(current_user.id, part_id, quantity, delivery_option, address)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("offer_detail", part_id=part_id))

        if delivery_option.strip().casefold() == DELIVERY_OPTION.casefold():
            flash(
                f"Zamowienie przyjete (ID: {order_id}). Doliczono 15 zl za dostawe, a sprzedawca skontaktuje sie z Toba w sprawie realizacji."
            )
        else:
            flash(
                f"Zamowienie przyjete (ID: {order_id}). Odbior osobisty nastapi po kontakcie ze sprzedawca."
            )
        return redirect(url_for("home"))

    def list_orders_for_buyer(buyer_id: int) -> list[Order]:
        with Session(engine) as session:
            query = (
                select(Order)
                .where(Order.buyer_id == buyer_id)
                .order_by(Order.created_at.desc())
            )
            return list(session.scalars(query))

    def list_orders_for_seller(seller_id: int) -> list[Order]:
        with Session(engine) as session:
            query = (
                select(Order)
                .join(Part, Part.id == Order.part_id)
                .where(Part.user_id == seller_id)
                .order_by(Order.created_at.desc())
            )
            return list(session.scalars(query))

    def count_unread_orders_for_seller(seller_id: int) -> int:
        with Session(engine) as session:
            query = (
                select(func.count())
                .select_from(Order)
                .join(Part, Part.id == Order.part_id)
                .where(Part.user_id == seller_id, Order.seller_unread == True)
            )
            result = session.scalar(query)
            return int(result or 0)

    def mark_unread_orders_for_seller_as_read(seller_id: int) -> None:
        with Session(engine) as session:
            query = (
                select(Order)
                .join(Part, Part.id == Order.part_id)
                .where(Part.user_id == seller_id, Order.seller_unread == True)
            )
            unread_orders = list(session.scalars(query))
            if not unread_orders:
                return

            for order in unread_orders:
                order.seller_unread = False

            session.commit()

    @app.context_processor
    def inject_unread_count():
        try:
            if current_user.is_authenticated:
                unread_orders = count_unread_orders_for_seller(current_user.id)
                with Session(engine) as session:
                    unread_msgs = session.scalar(
                        select(func.count(Message.id))
                        .join(Conversation, Message.conversation_id == Conversation.id)
                        .where(
                            Message.is_read == False,
                            Message.sender_id != current_user.id,
                            ((Conversation.buyer_id == current_user.id) | (Conversation.seller_id == current_user.id))
                        )
                    )
                return {
                    "seller_unread_count": unread_orders,
                    "unread_messages_count": unread_msgs or 0
                }
        except Exception:
            pass
        return {"seller_unread_count": 0, "unread_messages_count": 0}

    @app.route("/orders")
    @login_required
    def orders():
        orders = list_orders_for_buyer(current_user.id)
        # load parts for display
        parts = {o.part_id: get_part_by_id(o.part_id) for o in orders}
        with Session(engine) as session:
            reviews_by_order = {
                review.order_id: review
                for review in session.scalars(
                    select(Review).where(Review.reviewer_id == current_user.id)
                )
            }
        return render_template(
            "orders.html",
            orders=orders,
            parts=parts,
            reviews_by_order=reviews_by_order,
        )

    @app.route("/orders/<int:order_id>/review", methods=["GET", "POST"])
    @login_required
    def add_review(order_id: int):
        with Session(engine) as session:
            order = session.get(Order, order_id)
            if order is None or order.buyer_id != current_user.id:
                flash("Nie mozesz ocenic tej transakcji.")
                return redirect(url_for("orders"))

            if order.status != "zrealizowane":
                flash("Opinie mozna dodac po zrealizowaniu zamowienia.")
                return redirect(url_for("orders"))

            if order.seller_id is None:
                flash("Nie mozna ustalic sprzedawcy dla tej transakcji.")
                return redirect(url_for("orders"))

            if order.seller_id == current_user.id:
                flash("Nie mozesz ocenic samego siebie.")
                return redirect(url_for("orders"))

            if session.scalar(select(Review).where(Review.order_id == order.id)) is not None:
                flash("Ta transakcja zostala juz oceniona.")
                return redirect(url_for("orders"))

            part = session.get(Part, order.part_id)
            seller = session.get(User, order.seller_id)

            if request.method == "GET":
                return render_template(
                    "add_review.html",
                    order=order,
                    part=part,
                    seller=seller,
                )

            rating = parse_int(request.form.get("rating"), minimum=1, maximum=5)
            comment = clean_text(request.form.get("comment"), max_len=500)

            if rating is None or not comment:
                flash("Wybierz ocene od 1 do 5 i dodaj krotka opinie.")
                return redirect(url_for("add_review", order_id=order.id))

            session.add(
                Review(
                    order_id=order.id,
                    reviewer_id=current_user.id,
                    seller_id=order.seller_id,
                    rating=rating,
                    comment=comment,
                    created_at=time.time(),
                )
            )
            session.commit()

        flash("Dziekujemy za wystawienie opinii.")
        return redirect(url_for("orders"))

    # /seller/orders page removed — order management is available inside seller panel


    @app.route("/seller/orders/<int:order_id>/set_status", methods=["POST"]) 
    @login_required
    def set_order_status(order_id: int):
        new_status = clean_text(request.form.get("status"), max_len=50)

        with Session(engine) as session:
            order = session.get(Order, order_id)
            if order is None:
                flash("Zamowienie nie istnieje.")
                return redirect(url_for("seller_panel"))

            part = session.get(Part, order.part_id)
            if part is None or part.user_id != current_user.id:
                flash("Brak dostepu do tego zamowienia.")
                return redirect(url_for("seller_panel"))

            order.status = new_status
            session.commit()

        flash("Status zamowienia zostal zaktualizowany.")
        return redirect(url_for("seller_panel"))

    @app.route("/seller/<int:user_id>/offers")
    def seller_offers(user_id: int):
        seller = find_user_by_id(user_id)

        if seller is None:
            flash("Uzytkownik nie istnieje.")
            return redirect(url_for("home"))

        parts = list_public_parts_for_seller(user_id)
        return render_template(
            "seller_offers.html",
            seller=seller,
            parts=parts,
            seller_rating=get_seller_rating(user_id),
            seller_reviews=list_reviews_for_seller(user_id),
        )

    @app.route("/favorites")
    @login_required
    def favorites():
        parts = list_favorite_parts(current_user.id)
        return render_template("favorites.html", parts=parts)

    @app.route("/favorites/<int:part_id>/add", methods=["POST"])
    @login_required
    def add_favorite_route(part_id: int):
        part = get_part_by_id(part_id)

        if part is None or part.status not in PUBLIC_PART_STATUSES:
            flash("Oferta nie istnieje albo nie jest dostepna.")
            return redirect(url_for("home"))

        add_favorite(current_user.id, part_id)
        flash("Oferta zostala dodana do ulubionych.")
        return redirect(url_for("offer_detail", part_id=part_id))

    @app.route("/favorites/<int:part_id>/remove", methods=["POST"])
    @login_required
    def remove_favorite_route(part_id: int):
        remove_favorite(current_user.id, part_id)
        flash("Oferta zostala usunieta z ulubionych.")
        if request.form.get("redirect_to") == "offer":
            return redirect(url_for("offer_detail", part_id=part_id))

        return redirect(url_for("favorites"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = clean_text(request.form.get("username"), max_len=100)
            password = request.form.get("password") or ""
            repeated_password = request.form.get("repeated_password") or ""
            password_errors = validate_strong_password(password)
            if not username or not password:
                flash("Podaj nazwe uzytkownika i haslo.")
            elif password != repeated_password:
                flash("Hasla nie sa takie same.")
            elif password_errors:
                for error in password_errors:
                    flash(error)
            elif find_user_by_username(username) is not None:
                flash("Taki uzytkownik juz istnieje.")
            else:
                user_id = save_user(username, password)
                flask_session["setup_2fa_user_id"] = user_id
                flash("Konto zostalo utworzone. Skonfiguruj 2FA, aby dokonczyc rejestracje.")
                return redirect(url_for("setup_2fa"))

        return render_template("register.html")

    @app.route("/2fa/setup", methods=["GET", "POST"])
    def setup_2fa():
        if current_user.is_authenticated:
            user = current_user
            totp_secret = flask_session.get("pending_new_totp_secret")
            if not totp_secret:
                totp_secret = generate_totp_secret()
                flask_session["pending_new_totp_secret"] = totp_secret
        else:
            user_id = flask_session.get("setup_2fa_user_id")
            if not user_id:
                flash("Najpierw utworz konto.")
                return redirect(url_for("register"))
            user = find_user_by_id(int(user_id))
            if user is None or not user.totp_secret:
                flash("Nie mozna skonfigurowac 2FA dla tego konta.")
                return redirect(url_for("register"))
            totp_secret = decrypt_totp_secret(user.totp_secret)

        if request.method == "POST":
            code = request.form.get("totp_code") or ""

            if verify_totp_code(totp_secret, code):
                with Session(engine) as db_session:
                    db_user = db_session.get(User, user.id)
                    if db_user:
                        db_user.totp_secret = encrypt_totp_secret(totp_secret)
                        db_user.totp_confirmed = True
                        db_session.commit()

                if current_user.is_authenticated:
                    flask_session.pop("pending_new_totp_secret", None)
                    flash("2FA zostalo pomyslnie zaktualizowane.")
                    return redirect(url_for("settings"))
                else:
                    flask_session.pop("setup_2fa_user_id", None)
                    login_user(user)
                    flash("2FA zostalo skonfigurowane.")
                    return redirect(url_for("home"))

            flash("Nieprawidlowy kod 2FA.")

        qr_code = generate_qr_code_data_uri(user.username, totp_secret)

        return render_template(
            "setup_2fa.html",
            qr_code=qr_code,
            totp_secret=totp_secret,
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = clean_text(request.form.get("username"), max_len=100)
            password = request.form.get("password") or ""
            user = find_user_by_username(username)
            login_key = get_login_attempt_key(username)

            if is_login_blocked(engine, login_key):
                flash("Za duzo nieudanych prob logowania. Sprobuj ponownie za kilka minut.")
            elif user is None or not verify_password(user.password_hash, password):
                record_failed_login(engine, login_key)
                flash("Nieprawidlowa nazwa uzytkownika lub haslo.")
            else:
                clear_failed_logins(engine, login_key)

                if password_needs_argon2_rehash(user.password_hash):
                    update_user_password_hash(user.id, password)

                if not user.totp_secret or not user.totp_confirmed:
                    flash("Konto nie ma poprawnie skonfigurowanego 2FA.")
                    return redirect(url_for("login"))

                flask_session["pending_2fa_user_id"] = user.id
                return redirect(url_for("login_2fa"))

        return render_template("login.html")

    @app.route("/login/2fa", methods=["GET", "POST"])
    def login_2fa():
        user_id = flask_session.get("pending_2fa_user_id")

        if not user_id:
            flash("Najpierw podaj login i haslo.")
            return redirect(url_for("login"))

        user = find_user_by_id(int(user_id))

        if user is None or not user.totp_secret or not user.totp_confirmed:
            flask_session.pop("pending_2fa_user_id", None)
            flash("Nie mozna zweryfikowac 2FA.")
            return redirect(url_for("login"))

        if request.method == "POST":
            code = request.form.get("totp_code") or ""

            if verify_totp_code(decrypt_totp_secret(user.totp_secret), code):
                flask_session.pop("pending_2fa_user_id", None)
                login_user(user)
                return redirect(url_for("home"))

            flash("Nieprawidlowy kod 2FA.")

        return render_template("login_2fa.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        flask_session.pop("pending_2fa_user_id", None)
        flask_session.pop("setup_2fa_user_id", None)
        logout_user()
        return redirect(url_for("home"))

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        user = current_user

        if request.method == "POST":
            action = request.form.get("action")

            if action == "change_username":
                new_username = clean_text(request.form.get("username"), max_len=100)
                if not new_username:
                    flash("Nazwa uzytkownika nie moze byc pusta.")
                elif new_username == user.username:
                    flash("To jest Twoja aktualna nazwa uzytkownika.")
                elif find_user_by_username(new_username) is not None:
                    flash("Taka nazwa uzytkownika jest juz zajeta.")
                else:
                    with Session(engine) as db_session:
                        db_user = db_session.get(User, user.id)
                        if db_user:
                            db_user.username = new_username
                            db_session.execute(
                                text("UPDATE parts SET username = :new_username WHERE user_id = :user_id"),
                                {"new_username": new_username, "user_id": user.id}
                            )
                            db_session.commit()
                    flash("Nazwa uzytkownika zostala zmieniona.")
                    return redirect(url_for("settings"))

        return render_template(
            "settings.html",
            user=user,
        )


    @app.route("/add-part", methods=["GET", "POST"])
    @login_required
    def add_part():
        if request.method == "POST":
            part, errors = build_part_from_form(
                request.form,
                request.files,
                upload_dir,
                current_user.id,
                current_user.username,
            )

            if errors:
                for error in errors:
                    flash(error)
                return render_template("add_part.html", form_data=request.form)

            save_part(part)
            flash("Część została dodana.")
            return redirect(url_for("home"))

        return render_template("add_part.html", form_data={})

    @app.route("/seller-panel")
    @login_required
    def seller_panel():
        mark_unread_orders_for_seller_as_read(current_user.id)

        parts = list_parts_for_user(current_user.id)
        # only show visible offers (active, positive quantity, status 'aktywna') in the seller's offers table
        visible_parts = [p for p in parts if p.status == 'aktywna' and p.is_active and (p.quantity or 0) > 0]
        total_value = sum(part.price * part.quantity for part in visible_parts)
        # load orders for this seller and split into active vs realized
        orders = list_orders_for_seller(current_user.id)
        active_orders = [o for o in orders if o.status != "zrealizowane"]
        realized_orders = [o for o in orders if o.status == "zrealizowane"]

        stats = {
            "total_parts": len(visible_parts),
            "total_value": total_value,
            "realized_earnings": sum(o.total_price for o in realized_orders),
            "pending_earnings": sum(o.total_price for o in active_orders),
        }

        # load parts and buyers for orders mapping
        parts_map = {o.part_id: get_part_by_id(o.part_id) for o in orders}
        buyers = {}
        with Session(engine) as session:
            for o in orders:
                buyers[o.buyer_id] = session.get(User, o.buyer_id)

        return render_template(
            "seller_panel.html",
            parts=visible_parts,
            stats=stats,
            statuses=PART_STATUSES,
            active_orders=active_orders,
            realized_orders=realized_orders,
            orders_parts=parts_map,
            orders_buyers=buyers,
        )

    @app.route("/edit-part/<int:part_id>", methods=["GET", "POST"])
    @login_required
    def edit_part(part_id: int):
        part = get_part_by_id(part_id)

        if part is None or part.user_id != current_user.id:
            flash("Ta część nie istnieje lub nie masz do niej dostępu.")
            return redirect(url_for("seller_panel"))

        if request.method == "POST":
            data, errors = validate_part_form(request.form, require_status=True)

            if errors:
                for error in errors:
                    flash(error)
                apply_part_form_preview(part, request.form)
                return render_template("edit_part.html", part=part, statuses=PART_STATUSES)

            part.project_name = data["project_name"]
            part.category = data["category"]
            part.brand = data["brand"]
            part.condition = data["condition"]
            part.model = data["model"]
            part.year = data["year"]
            part.price = data["price"]
            part.quantity = data["quantity"]
            part.negotiable = data["negotiable"]
            part.status = data["status"]
            part.is_active = part.status in PUBLIC_PART_STATUSES
            part.vin = data["vin"] or None
            part.description = data["description"] or None

            existing_photos = part_photos(part)
            photos_to_delete = set(request.form.getlist("delete_photos"))
            new_photos = request.files.getlist("photo")

            for photo in new_photos:
                if photo and photo.filename and not is_allowed_photo(photo):
                    flash("Zdjęcia muszą być plikami PNG, JPG, JPEG, WEBP albo GIF.")
                    apply_part_form_preview(part, request.form)
                    return render_template("edit_part.html", part=part, statuses=PART_STATUSES)

            remaining_photos = [
                photo
                for photo in existing_photos
                if photo not in photos_to_delete
            ]

            for photo in existing_photos:
                if photo in photos_to_delete:
                    delete_uploaded_photo(photo, upload_dir)

            for photo in new_photos:
                if not photo or not photo.filename:
                    continue
                saved_photo = save_uploaded_photo(photo, upload_dir)
                if saved_photo:
                    remaining_photos.append(saved_photo)

            part.photo = serialize_part_photos(remaining_photos)

            update_part(part)
            flash("Część została zaktualizowana.")
            return redirect(url_for("seller_panel"))

        return render_template("edit_part.html", part=part, statuses=PART_STATUSES)

    @app.route("/delete-part/<int:part_id>", methods=["POST"])
    @login_required
    def delete_part_route(part_id: int):
        part = get_part_by_id(part_id)

        if part is None or part.user_id != current_user.id:
            flash("Ta część nie istnieje lub nie masz do niej dostępu.")
            return redirect(url_for("seller_panel"))

        delete_part(part_id)
        flash("Część została usunięta.")
        return redirect(url_for("seller_panel"))

    @app.route("/change-part-status/<int:part_id>", methods=["POST"])
    @login_required
    def change_part_status(part_id: int):
        part = get_part_by_id(part_id)

        if part is None or part.user_id != current_user.id:
            flash("Ta czesc nie istnieje lub nie masz do niej dostepu.")
            return redirect(url_for("seller_panel"))

        part.status = parse_part_status(request.form.get("status"))
        part.is_active = part.status in PUBLIC_PART_STATUSES
        update_part(part)
        flash("Status oferty zostal zmieniony.")
        return redirect(url_for("seller_panel"))

    @app.route("/health")
    def health():
        return {"status": "ok"}

    @app.route("/conversations/init/<int:part_id>", methods=["POST"])
    @login_required
    def init_conversation(part_id: int):
        with Session(engine) as session:
            part = session.get(Part, part_id)
            if not part:
                flash("Oferta nie istnieje.", "error")
                return redirect(url_for("home"))
            
            if part.user_id == current_user.id:
                flash("Nie możesz rozpocząć czatu z samym sobą.", "error")
                return redirect(url_for("offer_detail", part_id=part_id))

            query = select(Conversation).where(
                Conversation.part_id == part_id,
                Conversation.buyer_id == current_user.id,
                Conversation.seller_id == part.user_id
            )
            conv = session.scalar(query)
            
            if not conv:
                conv = Conversation(
                    part_id=part_id,
                    buyer_id=current_user.id,
                    seller_id=part.user_id,
                    created_at=time.time()
                )
                session.add(conv)
                session.commit()
                
            source = request.form.get("source")
            if source:
                return redirect(url_for("chat_room", conv_id=conv.id, source=source))
            return redirect(url_for("chat_room", conv_id=conv.id))

    @app.route("/conversations")
    @login_required
    def chat_list():
        with Session(engine) as session:
            buyer_query = select(Conversation, Part).join(Part, Conversation.part_id == Part.id).where(Conversation.buyer_id == current_user.id).order_by(Conversation.created_at.desc())
            buyer_convs = session.execute(buyer_query).all()

            seller_query = select(Conversation, Part).join(Part, Conversation.part_id == Part.id).where(Conversation.seller_id == current_user.id).order_by(Conversation.created_at.desc())
            seller_convs = session.execute(seller_query).all()
            
            return render_template("chat_list.html", buyer_convs=buyer_convs, seller_convs=seller_convs)

    @app.route("/conversations/<int:conv_id>")
    @login_required
    def chat_room(conv_id: int):
        with Session(engine) as session:
            conv = session.get(Conversation, conv_id)
            if not conv:
                flash("Konwersacja nie istnieje.", "error")
                return redirect(url_for("chat_list"))
                
            if current_user.id not in [conv.buyer_id, conv.seller_id]:
                flash("Brak dostępu.", "error")
                return redirect(url_for("chat_list"))
                
            part = session.get(Part, conv.part_id)
            
            source = request.args.get("source")
            if source == "home":
                back_url = url_for("home")
            elif source == "chat_list":
                back_url = url_for("chat_list")
            else:
                back_url = url_for("offer_detail", part_id=part.id)
            
            return render_template("chat.html", conv=conv, part=part, back_url=back_url)

    @app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
    @login_required
    def get_messages(conv_id: int):
        with Session(engine) as session:
            conv = session.get(Conversation, conv_id)
            if not conv or current_user.id not in [conv.buyer_id, conv.seller_id]:
                return {"error": "Unauthorized"}, 403
                
            since = request.args.get('since', 0, type=float)
            query = select(Message).where(
                Message.conversation_id == conv_id,
                Message.created_at > since
            ).order_by(Message.created_at.asc())
            
            messages = session.scalars(query).all()

            unread_msgs = [m for m in messages if m.sender_id != current_user.id and not m.is_read]
            if unread_msgs:
                for m in unread_msgs:
                    m.is_read = True
                session.commit()
            
            return {
                "messages": [{
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "content": m.content,
                    "message_type": m.message_type,
                    "price_offer": m.price_offer,
                    "created_at": m.created_at,
                    "is_read": m.is_read
                } for m in messages]
            }

    @app.route("/api/conversations/<int:conv_id>/messages", methods=["POST"])
    @login_required
    def send_message(conv_id: int):
        with Session(engine) as session:
            conv = session.get(Conversation, conv_id)
            if not conv or current_user.id not in [conv.buyer_id, conv.seller_id]:
                return {"error": "Unauthorized"}, 403
                
            data = request.json
            if not data:
                return {"error": "Bad request"}, 400
                
            content = data.get("content", "").strip()
            msg_type = data.get("message_type", "text")
            price_offer = data.get("price_offer")
            
            if conv.status == "closed" and msg_type == "offer":
                return {"error": "Nie można składać nowych ofert po sfinalizowaniu zakupu."}, 400
                
            if len(content) > 2000:
                return {"error": "Wiadomość za długa"}, 400
                
            msg = Message(
                conversation_id=conv_id,
                sender_id=current_user.id,
                content=content,
                message_type=msg_type,
                price_offer=float(price_offer) if price_offer else None,
                created_at=time.time(),
                is_read=False
            )
            session.add(msg)
            session.commit()
            
            return {"status": "ok", "message_id": msg.id}

    @app.route("/api/conversations/<int:conv_id>/messages/<int:msg_id>/<action>", methods=["POST"])
    @login_required
    def handle_offer(conv_id: int, msg_id: int, action: str):
        if action not in ["accept", "reject"]:
            return {"error": "Błędna akcja"}, 400
            
        with Session(engine) as session:
            conv = session.get(Conversation, conv_id)
            msg = session.get(Message, msg_id)
            
            if not conv or not msg or msg.conversation_id != conv.id:
                return {"error": "Nie znaleziono"}, 404
                
            if current_user.id not in [conv.buyer_id, conv.seller_id]:
                return {"error": "Unauthorized"}, 403
                
            if msg.sender_id == current_user.id:
                return {"error": "Nie możesz odrzucić/zaakceptować swojej oferty"}, 400
                
            if msg.message_type != "offer":
                return {"error": "Wiadomość nie należy do oferty (lub już rozpatrzona)"}, 400
                
            reply_type = "accept" if action == "accept" else "reject"
            msg.message_type = f"offer_{reply_type}"
            
            reply_msg = Message(
                conversation_id=conv_id,
                sender_id=current_user.id,
                content=f"Oferta {msg.price_offer} PLN została {'zaakceptowana' if action == 'accept' else 'odrzucona'}.",
                message_type=reply_type,
                price_offer=msg.price_offer,
                created_at=time.time(),
                is_read=False
            )
            session.add(reply_msg)
            session.commit()
            
            return {"status": "ok"}

    @app.route("/api/conversations/<int:conv_id>/buy", methods=["POST"])
    @login_required
    def buy_from_chat(conv_id: int):
        with Session(engine) as session:
            conv = session.get(Conversation, conv_id)
            if not conv or conv.buyer_id != current_user.id:
                return {"error": "Unauthorized"}, 403
                
            if conv.status == "closed":
                return {"error": "Zakup w ramach tej rozmowy został już sfinalizowany."}, 400
                
            data = request.json
            if not data:
                return {"error": "Bad request"}, 400
                
            quantity = int(data.get("quantity", 1))
            delivery_option = data.get("delivery_option", PICKUP_OPTION)
            city = data.get("city", "")
            postal_code = data.get("postal_code", "")
            street = data.get("street", "")
            house_number = data.get("house_number", "")
            apartment_number = data.get("apartment_number", "")
            
            if delivery_option == DELIVERY_OPTION:
                if not city or not postal_code or not street or not house_number:
                    return {"error": "Brakuje danych adresowych"}, 400
                address_line = f"{street} {house_number}"
                if apartment_number:
                    address_line = f"{address_line}/{apartment_number}"
                address = f"{postal_code} {city}, {address_line}"
            else:
                address = None

            try:
                order_id = create_order(current_user.id, conv.part_id, quantity, delivery_option, address)
                
                sys_msg = Message(
                    conversation_id=conv_id,
                    sender_id=current_user.id,
                    content=f"Kupujący zakupił przedmiot za wynegocjowaną cenę (Zamówienie #{order_id}).",
                    message_type="system",
                    created_at=time.time(),
                    is_read=False
                )
                session.add(sys_msg)
                conv.status = "closed"
                session.commit()
                
                return {"status": "ok", "order_id": order_id}
            except ValueError as e:
                return {"error": str(e)}, 400


    return app


def migrate_database(engine) -> None:
    with engine.begin() as connection:
        user_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(users)"))
        }
        part_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(parts)"))
        }

        if "role" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'seller'"))

        if "user_id" not in part_columns:
            connection.execute(text("ALTER TABLE parts ADD COLUMN user_id INTEGER"))

        if "is_active" not in part_columns:
            connection.execute(text("ALTER TABLE parts ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))

        if "status" not in part_columns:
            connection.execute(text("ALTER TABLE parts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'aktywna'"))
            connection.execute(
                text(
                    """
                    UPDATE parts
                    SET status = CASE
                        WHEN is_active = 1 THEN 'aktywna'
                        ELSE 'nieaktywny'
                    END
                    """
                )
            )

        connection.execute(
            text(
                """
                UPDATE parts
                SET user_id = (
                    SELECT users.id
                    FROM users
                    WHERE users.username = parts.username
                )
                WHERE user_id IS NULL
                """
            )
        )
        # Ensure new orders columns exist
        order_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(orders)"))
        }

        if "seller_unread" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN seller_unread BOOLEAN NOT NULL DEFAULT 1"))

        if "seller_id" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN seller_id INTEGER"))

        connection.execute(
            text(
                """
                UPDATE orders
                SET seller_id = (
                    SELECT parts.user_id
                    FROM parts
                    WHERE parts.id = orders.part_id
                )
                WHERE seller_id IS NULL
                """
            )
        )
        # Map old part status values to new 'nieaktywny' where appropriate
        connection.execute(
            text(
                """
                UPDATE parts
                SET status = 'nieaktywny'
                WHERE status IN ('zarezerwowana', 'sprzedana', 'ukryta')
                """
            )
        )


def build_part_from_form(form, files, upload_dir: Path, user_id: int, username: str) -> tuple[Part | None, list[str]]:
    data, errors = validate_part_form(form)
    photos = files.getlist("photo")

    if not user_id or not username:
        errors.append("Nie można ustalić właściciela oferty. Zaloguj się ponownie.")

    for photo in photos:
        if photo and photo.filename and not is_allowed_photo(photo):
            errors.append("Zdjęcia muszą być plikami PNG, JPG, JPEG, WEBP albo GIF.")
            break

    if errors:
        return None, errors

    photo_filenames = []
    for photo in photos:
        saved_photo = save_uploaded_photo(photo, upload_dir)
        if saved_photo:
            photo_filenames.append(saved_photo)

    return Part(
        user_id=user_id,
        username=username,
        project_name=data["project_name"],
        category=data["category"],
        brand=data["brand"],
        condition=data["condition"],
        model=data["model"],
        year=data["year"],
        price=data["price"],
        quantity=data["quantity"],
        negotiable=data["negotiable"],
        status="aktywna",
        is_active=True,
        vin=data["vin"] or None,
        description=data["description"] or None,
        photo=serialize_part_photos(photo_filenames),
    ), []


def validate_part_form(form, require_status: bool = False) -> tuple[dict[str, object], list[str]]:
    project_name = clean_text(form.get("project_name"), max_len=160)
    category = clean_text(form.get("category"), max_len=120)
    brand = clean_text(form.get("brand"), max_len=120)
    condition = clean_text(form.get("condition"), max_len=30)
    model = clean_text(form.get("model"), max_len=120)
    vin = clean_text(form.get("vin"), max_len=17)
    description = clean_text(form.get("description"), max_len=2000)

    year = parse_int(form.get("year"), minimum=1800, maximum=2100)
    price = parse_float(form.get("price"), minimum=0)
    quantity = parse_int(form.get("quantity"), minimum=1, maximum=9999)
    status = parse_part_status(form.get("status")) if require_status else "aktywna"

    errors = []
    required_text_fields = {
        "Nazwa": project_name,
        "Kategoria": category,
        "Marka/producent": brand,
        "Stan": condition,
        "Model auta": model,
    }
    for label, value in required_text_fields.items():
        if not value:
            errors.append(f"Pole '{label}' jest wymagane.")
        elif has_control_characters(value):
            errors.append(f"Pole '{label}' zawiera niedozwolone znaki sterujące.")

    if category and category not in PART_CATEGORIES:
        errors.append("Wybierz poprawną kategorię z listy.")
    if condition and condition not in PART_CONDITIONS:
        errors.append("Wybierz poprawny stan części.")
    if require_status and clean_text(form.get("status"), max_len=20) not in PART_STATUSES:
        errors.append("Wybierz poprawny status oferty.")

    if year is None:
        errors.append("Rocznik auta musi być liczbą od 1800 do 2100.")
    if price is None:
        errors.append("Cena musi być liczbą nieujemną.")
    if quantity is None:
        errors.append("Ilość sztuk musi być liczbą od 1 do 9999.")

    if vin and not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin.upper()):
        errors.append("VIN musi mieć 17 znaków i zawierać tylko dozwolone litery oraz cyfry.")
    if description and has_control_characters(description):
        errors.append("Opis zawiera niedozwolone znaki sterujące.")

    return {
        "project_name": project_name,
        "category": category,
        "brand": brand,
        "condition": condition,
        "model": model,
        "year": year,
        "price": price,
        "quantity": quantity,
        "negotiable": form.get("negotiable") == "on",
        "status": status,
        "vin": vin.upper(),
        "description": description,
    }, errors


def apply_part_form_preview(part: Part, form) -> None:
    part.project_name = clean_text(form.get("project_name"), max_len=160)
    part.category = clean_text(form.get("category"), max_len=120)
    part.brand = clean_text(form.get("brand"), max_len=120)
    part.condition = clean_text(form.get("condition"), max_len=30)
    part.model = clean_text(form.get("model"), max_len=120)
    part.year = form.get("year") or ""
    part.price = form.get("price") or ""
    part.quantity = form.get("quantity") or ""
    part.negotiable = form.get("negotiable") == "on"
    part.status = clean_text(form.get("status"), max_len=20) or "aktywna"
    part.vin = clean_text(form.get("vin"), max_len=17)
    part.description = clean_text(form.get("description"), max_len=2000)


def parse_part_status(value: str | None) -> str:
    if value in PART_STATUSES:
        return value

    return "aktywna"


def clean_text(value: str | None, max_len: int) -> str:
    if value is None:
        return ""

    return value.strip()[:max_len]


def parse_int(value: str | None, minimum: int, maximum: int) -> int | None:
    try:
        number = int(value or "")
    except ValueError:
        return None

    if number < minimum or number > maximum:
        return None

    return number


def parse_float(value: str | None, minimum: float) -> float | None:
    try:
        number = float(value or "")
    except ValueError:
        return None

    if not math.isfinite(number) or number < minimum:
        return None

    return round(number, 2)


def has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 and character not in ("\r", "\n", "\t") for character in value)


def is_allowed_photo(file) -> bool:
    original_filename = secure_filename(file.filename)
    if "." not in original_filename:
        return False

    extension = original_filename.rsplit(".", 1)[-1].lower()
    return extension in ALLOWED_EXTENSIONS


def part_photos(part: Part | None) -> list[str]:
    if part is None or not part.photo:
        return []

    return [
        photo
        for photo in part.photo.split(PHOTO_SEPARATOR)
        if photo
    ]


def primary_part_photo(part: Part | None) -> str | None:
    photos = part_photos(part)
    if not photos:
        return None

    return photos[0]


def serialize_part_photos(photos: list[str]) -> str | None:
    clean_photos = [photo for photo in photos if photo]
    if not clean_photos:
        return None

    return PHOTO_SEPARATOR.join(clean_photos)


def save_uploaded_photo(file, upload_dir: Path) -> str | None:
    if file is None or file.filename == "":
        return None

    original_filename = secure_filename(file.filename)
    if not is_allowed_photo(file):
        return None

    filename = f"{int(time.time() * 1000)}_{original_filename}"
    file.save(upload_dir / filename)

    return filename


def delete_uploaded_photo(filename: str, upload_dir: Path) -> None:
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return

    photo_path = upload_dir / safe_filename
    try:
        photo_path.unlink()
    except FileNotFoundError:
        pass


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
