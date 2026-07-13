from datetime import date
from functools import wraps
import sqlite3

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    add_trek_counts,
    close_db,
    execute,
    init_db,
    query_all,
    query_one,
    trek_by_id,
)


app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-for-production"
app.teardown_appcontext(close_db)


# ---------------- authentication ----------------


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def role_required(*roles):
    # one decorator so role checks dont get copied everywhere yaar
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if user["role"] not in roles:
                flash("You do not have permission to open that page.", "danger")
                return redirect(url_for("home"))
            if user["status"] == "blacklisted":
                session.clear()
                flash("This account is blacklisted.", "danger")
                return redirect(url_for("login"))
            if user["role"] == "staff" and user["status"] != "approved":
                flash("Your staff account is waiting for admin approval.", "warning")
                return redirect(url_for("logout"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def dashboard_url(role):
    return {
        "admin": "admin_dashboard",
        "staff": "staff_dashboard",
        "user": "user_dashboard",
    }[role]


@app.route("/")
def home():
    user = current_user()
    if user:
        return redirect(url_for(dashboard_url(user["role"])))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query_one("SELECT * FROM users WHERE email = ?", (email,))

        # if login fails dont leak which field was wrong
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))
        if user["status"] == "blacklisted":
            flash("This account is blacklisted.", "danger")
            return redirect(url_for("login"))
        if user["role"] == "staff" and user["status"] != "approved":
            flash("Staff account is pending admin approval.", "warning")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        flash(f"Welcome, {user['name']}!", "success")
        return redirect(url_for(dashboard_url(user["role"])))

    return render_template("auth/login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "")

        if role not in {"user", "staff"}:
            flash("Please choose User or Trek Staff.", "danger")
            return redirect(url_for("register"))
        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return redirect(url_for("register"))
        # password confirmation because humans type fast and regret faster
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        status = "pending" if role == "staff" else "active"
        try:
            # hash the password obviously
            execute(
                """
                INSERT INTO users (name, email, password_hash, role, phone, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, email, generate_password_hash(password), role, phone, status),
            )
        # email is unique dont fight it
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("register"))

        if role == "staff":
            flash("Registration successful. Please wait for admin approval.", "info")
        else:
            flash("Registration successful. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("auth/register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ---------------- admin dashboard ----------------


@app.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard():
    counts = {
        "treks": query_one("SELECT COUNT(*) AS c FROM treks")["c"],
        "users": query_one("SELECT COUNT(*) AS c FROM users WHERE role = 'user'")["c"],
        "staff": query_one("SELECT COUNT(*) AS c FROM users WHERE role = 'staff'")["c"],
        "bookings": query_one("SELECT COUNT(*) AS c FROM bookings")["c"],
    }
    recent_bookings = query_all(
        """
        SELECT b.*, u.name AS user_name, t.name AS trek_name
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        JOIN treks t ON t.id = b.trek_id
        ORDER BY b.id DESC
        LIMIT 5
        """
    )
    return render_template(
        "admin/dashboard.html", counts=counts, recent_bookings=recent_bookings
    )


@app.route("/admin/search")
@role_required("admin")
def admin_search():
    # users will type names ids locations basically anything here
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    treks = staff = users = []
    if q:
        treks = add_trek_counts(
            query_all(
                """
                SELECT t.*, u.name AS staff_name
                FROM treks t
                LEFT JOIN users u ON u.id = t.staff_id
                WHERE t.name LIKE ? OR t.location LIKE ? OR CAST(t.id AS TEXT) LIKE ?
                ORDER BY t.name
                """,
                (like, like, like),
            )
        )
        staff = query_all(
            """
            SELECT * FROM users
            WHERE role = 'staff'
              AND (name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) LIKE ?)
            ORDER BY name
            """,
            (like, like, like),
        )
        users = query_all(
            """
            SELECT * FROM users
            WHERE role = 'user'
              AND (name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) LIKE ?)
            ORDER BY name
            """,
            (like, like, like),
        )
    return render_template(
        "admin/search.html", q=q, treks=treks, staff=staff, users=users
    )


# ---------------- trek management ----------------


@app.route("/admin/treks")
@role_required("admin")
def admin_treks():
    q = request.args.get("q", "").strip()
    params = []
    where = ""
    if q:
        where = """
            WHERE t.name LIKE ? OR t.location LIKE ? OR t.difficulty LIKE ?
                  OR CAST(t.id AS TEXT) LIKE ?
        """
        like = f"%{q}%"
        params = [like, like, like, like]
    treks = query_all(
        f"""
        SELECT t.*, u.name AS staff_name
        FROM treks t
        LEFT JOIN users u ON u.id = t.staff_id
        {where}
        ORDER BY t.start_date
        """,
        params,
    )
    return render_template("admin/treks.html", treks=add_trek_counts(treks), q=q)


@app.route("/admin/treks/new", methods=["GET", "POST"])
@role_required("admin")
def admin_trek_new():
    return save_trek()


@app.route("/admin/treks/<int:trek_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def admin_trek_edit(trek_id):
    return save_trek(trek_id)


def save_trek(trek_id=None):
    trek = trek_by_id(trek_id) if trek_id else None
    if trek_id and not trek:
        flash("Trek not found.", "danger")
        return redirect(url_for("admin_treks"))

    # approved staff only warna dropdown gets messy
    staff = query_all(
        "SELECT id, name FROM users WHERE role = 'staff' AND status = 'approved' ORDER BY name"
    )

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        difficulty = request.form.get("difficulty", "Easy")
        duration_days = int(request.form.get("duration_days") or 0)
        total_slots = int(request.form.get("total_slots") or 0)
        staff_id = request.form.get("staff_id") or None
        status = request.form.get("status", "Open")
        start_date = request.form.get("start_date", "")
        end_date = request.form.get("end_date", "")
        description = request.form.get("description", "").strip()

        if not name or not location or duration_days <= 0 or total_slots <= 0:
            flash("Please fill all required trek fields.", "danger")
            return redirect(request.url)
        if difficulty not in {"Easy", "Moderate", "Hard"}:
            flash("Invalid difficulty selected.", "danger")
            return redirect(request.url)
        if status not in {"Pending", "Approved", "Open", "Closed", "Started", "Completed"}:
            flash("Invalid trek status selected.", "danger")
            return redirect(request.url)
        # make sure dates actually make sense yaar
        if not start_date or not end_date or end_date < start_date:
            flash("End date must be on or after start date.", "danger")
            return redirect(request.url)
        # cant reduce slots below active bookings math hai boss
        if trek and total_slots < trek["booked_count"]:
            flash("Slots cannot be less than current active bookings.", "danger")
            return redirect(request.url)

        params = (
            name,
            location,
            difficulty,
            duration_days,
            total_slots,
            staff_id,
            status,
            start_date,
            end_date,
            description,
        )
        if trek:
            execute(
                """
                UPDATE treks
                SET name = ?, location = ?, difficulty = ?, duration_days = ?,
                    total_slots = ?, staff_id = ?, status = ?, start_date = ?,
                    end_date = ?, description = ?
                WHERE id = ?
                """,
                params + (trek_id,),
            )
            flash("Trek updated successfully.", "success")
        else:
            execute(
                """
                INSERT INTO treks
                (name, location, difficulty, duration_days, total_slots, staff_id,
                 status, start_date, end_date, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            flash("Trek created successfully.", "success")
        return redirect(url_for("admin_treks"))

    return render_template("admin/trek_form.html", trek=trek, staff=staff)


@app.route("/admin/treks/<int:trek_id>/delete", methods=["POST"])
@role_required("admin")
def admin_trek_delete(trek_id):
    active_or_old_bookings = query_one(
        "SELECT COUNT(*) AS c FROM bookings WHERE trek_id = ?", (trek_id,)
    )["c"]
    # keep booking history so close the trek instead of deleting it
    if active_or_old_bookings:
        execute("UPDATE treks SET status = 'Closed' WHERE id = ?", (trek_id,))
        flash("Trek has booking history, so it was closed instead of deleted.", "warning")
    else:
        execute("DELETE FROM treks WHERE id = ?", (trek_id,))
        flash("Trek removed successfully.", "success")
    return redirect(url_for("admin_treks"))


# ---------------- account management ----------------


@app.route("/admin/staff")
@role_required("admin")
def admin_staff():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    params = (like, like, like) if q else ()
    where = "AND (name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) LIKE ?)" if q else ""
    staff = query_all(
        f"""
        SELECT * FROM users
        WHERE role = 'staff' {where}
        ORDER BY
            CASE status WHEN 'pending' THEN 1 WHEN 'approved' THEN 2 ELSE 3 END,
            name
        """,
        params,
    )
    return render_template("admin/people.html", people=staff, person_type="staff", q=q)


@app.route("/admin/users")
@role_required("admin")
def admin_users():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    params = (like, like, like) if q else ()
    where = "AND (name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) LIKE ?)" if q else ""
    users = query_all(
        f"""
        SELECT * FROM users
        WHERE role = 'user' {where}
        ORDER BY name
        """,
        params,
    )
    return render_template("admin/people.html", people=users, person_type="user", q=q)


@app.route("/admin/users/<int:user_id>/status", methods=["POST"])
@role_required("admin")
def admin_user_status(user_id):
    user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or user["role"] == "admin":
        flash("Account not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    # blacklist != delete
    action = request.form.get("action")
    if action == "approve" and user["role"] == "staff":
        new_status = "approved"
    elif action == "blacklist":
        new_status = "blacklisted"
    elif action == "activate":
        new_status = "approved" if user["role"] == "staff" else "active"
    else:
        flash("Invalid action.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))

    execute("UPDATE users SET status = ? WHERE id = ?", (new_status, user_id))
    flash(f"{user['name']} status updated to {new_status}.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/bookings")
@role_required("admin")
def admin_bookings():
    bookings = query_all(
        """
        SELECT b.*, u.name AS user_name, t.name AS trek_name
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        JOIN treks t ON t.id = b.trek_id
        ORDER BY b.booking_date DESC, b.id DESC
        """
    )
    return render_template("admin/bookings.html", bookings=bookings)


# ---------------- staff dashboard ----------------


@app.route("/staff/dashboard")
@role_required("staff")
def staff_dashboard():
    treks = query_all(
        """
        SELECT t.*, u.name AS staff_name
        FROM treks t
        LEFT JOIN users u ON u.id = t.staff_id
        WHERE t.staff_id = ?
        ORDER BY t.start_date
        """,
        (session["user_id"],),
    )
    treks = add_trek_counts(treks)
    stats = {
        "assigned": len(treks),
        "participants": sum(t["booked_count"] for t in treks),
        "open": sum(1 for t in treks if t["status"] == "Open"),
    }
    return render_template("staff/dashboard.html", treks=treks, stats=stats)


@app.route("/staff/treks/<int:trek_id>", methods=["GET", "POST"])
@role_required("staff")
def staff_trek_detail(trek_id):
    trek = trek_by_id(trek_id)
    if not trek or trek["staff_id"] != session["user_id"]:
        flash("Only the assigned staff member can manage this trek.", "danger")
        return redirect(url_for("staff_dashboard"))

    if request.method == "POST":
        total_slots = int(request.form.get("total_slots") or 0)
        status = request.form.get("status", "Open")
        # dont let slots go below active bookings ayyo
        if total_slots < trek["booked_count"]:
            flash("Slots cannot be less than current active bookings.", "danger")
            return redirect(url_for("staff_trek_detail", trek_id=trek_id))
        if status not in {"Open", "Closed", "Started", "Completed"}:
            flash("Invalid status selected.", "danger")
            return redirect(url_for("staff_trek_detail", trek_id=trek_id))

        execute(
            "UPDATE treks SET total_slots = ?, status = ? WHERE id = ?",
            (total_slots, status, trek_id),
        )
        # completed trek = completed bookings too
        if status == "Completed":
            execute(
                "UPDATE bookings SET status = 'Completed' WHERE trek_id = ? AND status = 'Booked'",
                (trek_id,),
            )
        flash("Trek updated successfully.", "success")
        return redirect(url_for("staff_trek_detail", trek_id=trek_id))

    participants = query_all(
        """
        SELECT b.*, u.name AS user_name, u.email, u.phone
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        WHERE b.trek_id = ?
        ORDER BY b.booking_date DESC
        """,
        (trek_id,),
    )
    return render_template(
        "staff/trek_detail.html", trek=trek, participants=participants
    )


# ---------------- user dashboard ----------------


@app.route("/user/dashboard")
@role_required("user")
def user_dashboard():
    q = request.args.get("q", "").strip()
    difficulty = request.args.get("difficulty", "").strip()
    location = request.args.get("location", "").strip()

    # optional filters keep this in one simple route
    where = ["t.status = 'Open'"]
    params = []
    if q:
        where.append("(t.name LIKE ? OR t.location LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if difficulty:
        where.append("t.difficulty = ?")
        params.append(difficulty)
    if location:
        where.append("t.location = ?")
        params.append(location)

    treks = query_all(
        f"""
        SELECT t.*, u.name AS staff_name
        FROM treks t
        LEFT JOIN users u ON u.id = t.staff_id
        WHERE {' AND '.join(where)}
        ORDER BY t.start_date
        """,
        params,
    )
    locations = query_all(
        "SELECT DISTINCT location FROM treks WHERE status = 'Open' ORDER BY location"
    )
    bookings = query_all(
        """
        SELECT b.*, t.name AS trek_name, t.start_date, t.end_date
        FROM bookings b
        JOIN treks t ON t.id = b.trek_id
        WHERE b.user_id = ?
        ORDER BY b.id DESC
        LIMIT 5
        """,
        (session["user_id"],),
    )
    return render_template(
        "user/dashboard.html",
        treks=add_trek_counts(treks),
        bookings=bookings,
        locations=locations,
        q=q,
        difficulty=difficulty,
        location=location,
    )


# ---------------- booking logic ----------------


@app.route("/user/treks/<int:trek_id>", methods=["GET", "POST"])
@role_required("user")
def user_trek_detail(trek_id):
    trek = trek_by_id(trek_id)
    if not trek:
        flash("Trek not found.", "danger")
        return redirect(url_for("user_dashboard"))

    # one active booking per user per trek bas
    existing = query_one(
        """
        SELECT id FROM bookings
        WHERE user_id = ? AND trek_id = ? AND status = 'Booked'
        """,
        (session["user_id"], trek_id),
    )

    if request.method == "POST":
        if trek["status"] != "Open":
            flash("Only open treks can be booked.", "danger")
        elif existing:
            flash("You already have an active booking for this trek.", "warning")
        # full means full mountains arent expandable
        elif trek["remaining_slots"] <= 0:
            flash("No slots are available for this trek.", "danger")
        else:
            execute(
                """
                INSERT INTO bookings (user_id, trek_id, booking_date, status)
                VALUES (?, ?, ?, 'Booked')
                """,
                (session["user_id"], trek_id, date.today().isoformat()),
            )
            flash("Trek booked successfully.", "success")
        return redirect(url_for("user_trek_detail", trek_id=trek_id))

    return render_template("user/trek_detail.html", trek=trek, existing=existing)


@app.route("/user/bookings")
@role_required("user")
def user_bookings():
    bookings = query_all(
        """
        SELECT b.*, t.name AS trek_name, t.location, t.start_date, t.end_date
        FROM bookings b
        JOIN treks t ON t.id = b.trek_id
        WHERE b.user_id = ?
        ORDER BY b.booking_date DESC, b.id DESC
        """,
        (session["user_id"],),
    )
    return render_template("user/bookings.html", bookings=bookings, history=False)


@app.route("/user/history")
@role_required("user")
def user_history():
    bookings = query_all(
        """
        SELECT b.*, t.name AS trek_name, t.location, t.start_date, t.end_date
        FROM bookings b
        JOIN treks t ON t.id = b.trek_id
        WHERE b.user_id = ? AND b.status IN ('Completed', 'Cancelled')
        ORDER BY b.booking_date DESC, b.id DESC
        """,
        (session["user_id"],),
    )
    return render_template("user/bookings.html", bookings=bookings, history=True)


@app.route("/user/bookings/<int:booking_id>/cancel", methods=["POST"])
@role_required("user")
def user_cancel_booking(booking_id):
    booking = query_one(
        "SELECT * FROM bookings WHERE id = ? AND user_id = ?",
        (booking_id, session["user_id"]),
    )
    if not booking:
        flash("Booking not found.", "danger")
    elif booking["status"] != "Booked":
        flash("Only active bookings can be cancelled.", "warning")
    else:
        execute("UPDATE bookings SET status = 'Cancelled' WHERE id = ?", (booking_id,))
        flash("Booking cancelled.", "success")
    return redirect(url_for("user_bookings"))


@app.route("/profile", methods=["GET", "POST"])
@role_required("staff", "user")
def profile():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if not name:
            flash("Name is required.", "danger")
            return redirect(url_for("profile"))
        # email stays forever identity crisis later
        execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, user["id"]))
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("user/profile.html", user=user)


# ---------------- api ----------------


@app.route("/api/treks")
def api_treks():
    treks = add_trek_counts(
        query_all(
            """
            SELECT t.*, u.name AS staff_name
            FROM treks t
            LEFT JOIN users u ON u.id = t.staff_id
            ORDER BY t.start_date
            """
        )
    )
    return jsonify(treks)


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True)
