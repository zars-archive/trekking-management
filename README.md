# Trailmark Trekking Management

Basic Flask web app for the App Dev I trekking management problem statement.

## Project structure

```text
trekking_management_app/
|-- static/
|   |-- css/
|   |   `-- style.css
|   `-- images/
|       `-- trailmark-mountains.webp
|-- templates/
|   |-- admin/
|   |-- auth/
|   |-- staff/
|   |-- user/
|   `-- base.html
|-- api.yaml
|-- app.py
|-- models.py
|-- requirements.txt
`-- README.md
```

- `app.py` contains Flask routes, authentication, and role-based workflows.
- `models.py` contains SQLite connection helpers, tables, seed data, and trek queries.
- `templates` keeps pages separated by user role.
- `static` keeps stylesheets and images in separate folders.

## Stack

- Flask backend
- Jinja2 templates
- Bootstrap and custom CSS frontend
- SQLite database created programmatically

## How to run

```bash
cd trekking_management_app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Admin | admin@example.com | admin123 |
| Trek Staff | staff@example.com | staff123 |
| User | user@example.com | user123 |

## Implemented features

- Admin can create, edit, remove or close treks, approve/blacklist staff, blacklist users, assign staff, view bookings, and search records.
- Staff can log in after approval, view assigned treks, update slots/status, and view participants.
- Users can register, browse/filter open treks, book treks, cancel bookings, view booking status/history, and edit profile.
- Booking is blocked when a trek is not open, already booked by the same user, or out of slots.
- Database tables are created automatically in `trekking.db` on first run.

