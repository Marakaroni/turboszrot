# TurboSzrot — marketplace części samochodowych

Aplikacja webowa do kupna i sprzedaży używanych części samochodowych, stworzona zespołowo w ramach przedmiotu *Projekt zespołowy* (Politechnika Warszawska, 2026).

## Funkcje
- wyszukiwanie części i przeglądanie ofert, ulubione
- panel sprzedawcy: dodawanie, edycja i statusy ofert, zamówienia
- czat kupującego ze sprzedawcą (negocjacja ceny, zakup z poziomu rozmowy)
- system ocen i opinii
- rejestracja i logowanie z **uwierzytelnianiem dwuskładnikowym (TOTP, kod QR)**

## Moja część
- walidacja formularzy po stronie serwera i komunikaty błędów
- logowanie dwuetapowe (2FA): generowanie i szyfrowanie sekretów TOTP, kody QR, weryfikacja
- konteneryzacja (Dockerfile, Docker Compose) i konfiguracja **Nginx** jako reverse proxy z HTTPS

## Stos technologiczny
Python · Flask · Flask-Login · SQLAlchemy · SQLite · Argon2 · pyotp · Gunicorn · Docker Compose · Nginx

## Bezpieczeństwo
- hasła haszowane Argon2, blokada po wielu nieudanych logowaniach
- sekrety TOTP szyfrowane (Fernet)
- limit i whitelist typów uploadu, `secure_filename`, `TRUSTED_HOSTS`, `ProxyFix`
- nagłówki bezpieczeństwa w Nginx, aplikacja działa w kontenerze jako użytkownik bez uprawnień root

## Uruchomienie
```bash
cp .env.example .env          # uzupełnij SECRET_KEY i ENCRYPTION_KEY
# certyfikat testowy dla Nginx:
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout nginx/ssl/certs/server.key -out nginx/ssl/certs/server.cert -subj "/CN=localhost"
docker compose up --build
```
Aplikacja będzie dostępna pod `https://localhost`. Dane demonstracyjne: `python -m app.seed_demo`.
