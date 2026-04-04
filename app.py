# ============================================================
#  HACK4GOV - CCS WEEK 2025 | Basilan State College
#  Flask Backend - app.py  (with Admin Dashboard)
# ============================================================

from flask import (
    Flask, request, jsonify,
    redirect, url_for,
    render_template,
    session,          # session → stores login state across requests
                      # Think of it like a "memory" that Flask keeps
                      # for each visitor. Once you log in, Flask
                      # remembers you until you log out or close the browser.
    flash             # flash  → sends a one-time message to the next page
                      # e.g., "Wrong password!" shown after a failed login
)
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import os
import csv
import io
from functools import wraps
# csv, io      → used to build the CSV export file in memory
# functools    → used to build the login_required decorator (explained below)

app = Flask(__name__)

# ============================================================
#  SECRET KEY — required for session to work
#  session uses a cookie, and this key signs/encrypts it.
#  Change this to any long random string before deploying!
# ============================================================
app.secret_key = 'change-this-to-something-random-before-deploying'
# TODO: replace with something like: 'h4ck4g0v_ccs_bsc_2025_super_secret'


# ============================================================
#  ADMIN CREDENTIALS
#  Hardcoded here for simplicity. Keep this file private!
#  TODO: you can move these to environment variables later.
# ============================================================
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'hack4gov2026'   # TODO: change this password!


# ============================================================
#  DATABASE SETUP  (same as before)
# ============================================================
DB_PATH = os.path.join(os.path.dirname(__file__), 'hack4gov.db')

def get_db():
    # Longer timeout + busy handler reduce "database is locked" under concurrent requests
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Extra guard in case the process was started before init_db ran
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

def init_db():
    conn = get_db()
    conn.execute("PRAGMA journal_mode = WAL;")      # better read/write concurrency
    conn.execute("PRAGMA synchronous = NORMAL;")    # safer default for WAL in web use
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            age            INTEGER NOT NULL,
            course_year    TEXT    NOT NULL,
            contact_number TEXT    NOT NULL,
            email          TEXT    NOT NULL UNIQUE,
            confirmed      INTEGER DEFAULT 0,
            token          TEXT    NOT NULL,
            registered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ============================================================
#  EMAIL SETUP  (same as before)
# ============================================================
EMAIL_SENDER   = 'kleia6678@gmail.com'   # TODO: your Gmail
EMAIL_PASSWORD = 'smwqemlpykznlvxh'      # TODO: Gmail App Password
BASE_URL       = 'http://localhost:5000'  # TODO: change when deploying

def send_confirmation_email(to_email, name, token):
    confirm_url = f"{BASE_URL}/confirm/{token}"
    msg = MIMEMultipart('alternative')
    msg['Subject'] = '[ HACK4GOV ] Confirm Your Registration'
    msg['From']    = EMAIL_SENDER
    msg['To']      = to_email
    text_body = f"Hi {name},\n\nConfirm your registration here:\n{confirm_url}\n\nSee you April 21-23!\n— CCS, Basilan State College"
    html_body = f"""
    <div style="font-family:monospace;background:#0a0a0a;color:#f0f0f0;padding:32px;max-width:500px;">
        <div style="background:#8B1A1A;padding:10px 20px;margin-bottom:20px;">
            <span style="letter-spacing:4px;font-size:18px;font-weight:bold;">HACK4GOV</span>
            <span style="float:right;font-size:11px;color:#ffd0d0;">CCS WEEK 2025</span>
        </div>
        <p>Hi <strong style="color:#c0392b">{name}</strong>,</p>
        <p style="color:#ccc;">Click below to confirm your registration and lock in your slot.</p>
        <a href="{confirm_url}" style="display:inline-block;margin:20px 0;padding:12px 28px;background:#8B1A1A;color:#fff;text-decoration:none;letter-spacing:2px;font-size:14px;font-weight:bold;">[ CONFIRM MY REGISTRATION ]</a>
        <p style="color:#555;font-size:11px;">Or copy: <a href="{confirm_url}" style="color:#8B1A1A;">{confirm_url}</a></p>
        <hr style="border-color:#2a0a0a;margin:20px 0;">
        <p style="color:#444;font-size:11px;">COLLEGE OF COMPUTING STUDIES — BASILAN STATE COLLEGE | APR 21–23, 2025</p>
    </div>"""
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


# ============================================================
#  LOGIN REQUIRED DECORATOR
#
#  A decorator is a function that "wraps" another function
#  to add extra behavior before it runs.
#
#  @login_required above a route means:
#  "Before running this route, check if the admin is logged in.
#   If not, send them to the login page instead."
#
#  This saves us from writing the same if-check in every route.
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # session is like a dictionary Flask stores per visitor
        # 'admin_logged_in' is a key we set ourselves during login
        if not session.get('admin_logged_in'):
            # flash sends a message that appears on the next page
            flash('Please log in to access the admin panel.')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated
# Usage: just put @login_required above any route you want to protect


# ============================================================
#  PUBLIC ROUTES  (same as before)
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/participants')
def participants_page():
    return render_template('participants.html')

@app.route('/api/register', methods=['POST'])
def register():
    data           = request.get_json()
    name           = data.get('name', '').strip()
    age            = data.get('age', '').strip()
    course_year    = data.get('course_year', '').strip()
    contact_number = data.get('contact_number', '').strip()
    email          = data.get('email', '').strip()

    if not all([name, age, course_year, contact_number, email]):
        return jsonify({'success': False, 'message': 'All fields are required.'})

    token = secrets.token_urlsafe(32)

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO registrations (name, age, course_year, contact_number, email, token)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, age, course_year, contact_number, email, token))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': 'This email is already registered.'})
    except Exception as e:
        print(f"DB error: {e}")
        conn.close()
        return jsonify({'success': False, 'message': 'Database error. Please try again.'})
    else:
        conn.close()

    email_sent = send_confirmation_email(email, name, token)
    if not email_sent:
        return jsonify({'success': True, 'message': 'Registered! But confirmation email failed. Contact organizers.'})

    return jsonify({'success': True})

@app.route('/api/participants')
def get_participants():
    conn  = get_db()
    try:
        rows  = conn.execute("""
            SELECT name, course_year FROM registrations
            WHERE confirmed = 1 ORDER BY registered_at ASC
        """).fetchall()
    except Exception as e:
        print(f"DB error: {e}")
        conn.close()
        return jsonify({'participants': []})
    else:
        conn.close()
    return jsonify({'participants': [dict(r) for r in rows]})

@app.route('/confirm/<token>')
def confirm_email(token):
    conn = get_db()
    try:
        row  = conn.execute("""
            SELECT id FROM registrations WHERE token = ? AND confirmed = 0
        """, (token,)).fetchone()
        if row is None:
            conn.close()
            return "Invalid or already used confirmation link.", 400
        conn.execute("UPDATE registrations SET confirmed = 1 WHERE token = ?", (token,))
        conn.commit()
    except Exception as e:
        print(f"DB error: {e}")
        conn.close()
        return "Something went wrong. Contact the organizers.", 500
    else:
        conn.close()
    return redirect(url_for('participants_page'))


# ============================================================
#  ADMIN ROUTES
# ============================================================

# ------------------------------------------------------------
#  ADMIN LOGIN  —  GET /admin/login   shows the login form
#                  POST /admin/login  processes the form
# ------------------------------------------------------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():

    # GET → just show the login page
    if request.method == 'GET':
        return render_template('admin/admin_login.html')

    # POST → the login form was submitted
    # request.form reads data from an HTML <form> submission
    # (different from request.get_json() which reads JSON from JavaScript)
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # Credentials correct — store login state in session
        session['admin_logged_in'] = True
        session['admin_username']  = username
        # Now every future request from this browser will have
        # session['admin_logged_in'] == True until they log out

        return redirect(url_for('admin_dashboard'))

    else:
        # Wrong credentials — flash an error and reload the login page
        flash('Invalid username or password.')
        return redirect(url_for('admin_login'))


# ------------------------------------------------------------
#  ADMIN LOGOUT
# ------------------------------------------------------------
@app.route('/admin/logout')
def admin_logout():
    # session.clear() removes ALL session data (logs the admin out)
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('admin_login'))


# ------------------------------------------------------------
#  ADMIN DASHBOARD  —  the main page with stats + table
# ------------------------------------------------------------
@app.route('/admin')
@login_required   # ← protects this route — redirects to login if not logged in
def admin_dashboard():
    conn = get_db()
    try:

        # Get all registrations (both confirmed and pending)
        all_rows = conn.execute("""
            SELECT id, name, age, course_year, contact_number,
                   email, confirmed, registered_at
            FROM registrations
            ORDER BY registered_at DESC
        """).fetchall()

        # Count stats using SQL aggregate functions
        # COUNT(*) → counts all rows
        # SUM(confirmed) → adds up all 1s (confirmed) to get total confirmed
        stats = conn.execute("""
            SELECT
                COUNT(*)        AS total,
                SUM(confirmed)  AS confirmed,
                COUNT(*) - SUM(confirmed) AS pending
            FROM registrations
        """).fetchone()

    except Exception as e:
        print(f"DB error: {e}")
        all_rows = []
        stats    = {'total': 0, 'confirmed': 0, 'pending': 0}
        conn.close()
    else:
        conn.close()

    # Calculate confirm rate (avoid division by zero with `or 1`)
    total     = stats['total']     or 0
    confirmed = stats['confirmed'] or 0
    pending   = stats['pending']   or 0
    rate      = round((confirmed / total * 100)) if total > 0 else 0

    # Pass all this data to the HTML template
    # In Jinja2 (Flask's template engine), {{ total }} in the HTML
    # will be replaced by the value of total from here
    return render_template('/admin/admin_dashboard.html',
        registrations = [dict(r) for r in all_rows],
        total         = total,
        confirmed     = confirmed,
        pending       = pending,
        rate          = rate,
        admin_user    = session.get('admin_username', 'admin')
    )


# ------------------------------------------------------------
#  EXPORT CSV  —  downloads all registrations as a .csv file
# ------------------------------------------------------------
@app.route('/admin/export')
@login_required
def admin_export():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT name, age, course_year, contact_number, email,
                   CASE WHEN confirmed = 1 THEN 'Confirmed' ELSE 'Pending' END AS status,
                   registered_at
            FROM registrations
            ORDER BY registered_at ASC
        """).fetchall()
    except Exception as e:
        print(f"DB error: {e}")
        conn.close()
        return "Error generating export.", 500
    else:
        conn.close()

    # Build the CSV in memory (no need to save a file on disk)
    # io.StringIO() creates an in-memory text buffer (like a fake file)
    output = io.StringIO()
    writer = csv.writer(output)

    # Write the header row
    writer.writerow(['Name', 'Age', 'Course/Year', 'Contact Number', 'Email', 'Status', 'Registered At'])

    # Write one row per registration
    for row in rows:
        writer.writerow([
            row['name'],
            row['age'],
            row['course_year'],
            row['contact_number'],
            row['email'],
            row['status'],
            row['registered_at']
        ])

    # Get the CSV string from the buffer
    csv_data = output.getvalue()
    output.close()

    # Send the CSV as a downloadable file response
    # make_response builds a custom HTTP response
    from flask import make_response
    response = make_response(csv_data)
    response.headers['Content-Type']        = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=hack4gov_registrations.csv'
    # Content-Disposition: attachment → tells the browser to download it
    # filename= → the default filename when saving
    return response


# ============================================================
#  INITIALIZE DB + RUN
# ============================================================
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port="5000")
    # TODO: set debug=False before deploying to PythonAnywhere!
