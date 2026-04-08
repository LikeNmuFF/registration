import os
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
from datetime import datetime
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import os
import csv
import io
from functools import wraps
from dotenv import load_dotenv
# csv, io      → used to build the CSV export file in memory
# functools    → used to build the login_required decorator (explained below)

app = Flask(__name__)

# Load .env file explicitly (required for PythonAnywhere / local dev)
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app.secret_key = os.getenv('SECRET_KEY', secrets.token_urlsafe(16))  # Used to secure sessions and flash messages

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
# Max number of participants allowed; controls slot counter + lock
MAX_PARTICIPANTS = int(os.getenv('MAX_PARTICIPANTS', 30))
# ============================================================
#  DATABASE SETUP 
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
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")    # safer default for WAL in web use
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            codename       TEXT,
            age            INTEGER NOT NULL,
            course_year    TEXT    NOT NULL,
            contact_number TEXT    NOT NULL,
            email          TEXT    NOT NULL UNIQUE,
            confirmed      INTEGER DEFAULT 0,
            token          TEXT    NOT NULL,
            registered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Add codename column if migrating an existing DB
    cols = {row[1] for row in conn.execute("PRAGMA table_info(registrations)")}
    if 'codename' not in cols:
        conn.execute("ALTER TABLE registrations ADD COLUMN codename TEXT;")
    conn.commit()
    conn.close()


# ============================================================
#  EMAIL SETUP 
# ============================================================
EMAIL_SENDER   = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')     
BASE_URL       = 'https://hack4gov.pythonanywhere.com' 

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
        print(f"Email sent successfully to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"Email AUTH error (check EMAIL_SENDER/EMAIL_PASSWORD): {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"Email SMTP error: {e}")
        return False
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


@app.route('/api/registration-status')
def registration_status():
    """
    Returns live slot availability for the landing page.
    Payload example:
      { "max_participants": 20, "confirmed_count": 7, "is_open": true }
    """
    conn = get_db()
    try:
        confirmed_count = conn.execute("""
            SELECT COUNT(*) AS c FROM registrations WHERE confirmed = 1
        """).fetchone()['c']
        total_count = conn.execute("""
            SELECT COUNT(*) AS c FROM registrations
        """).fetchone()['c']
    except Exception as e:
        print(f"DB error: {e}")
        confirmed_count = 0
        total_count = 0
    finally:
        conn.close()

    # Registration is considered open until confirmed slots hit the cap
    is_open = confirmed_count < MAX_PARTICIPANTS

    return jsonify({
        'max_participants': MAX_PARTICIPANTS,
        'confirmed_count': confirmed_count,
        'pending_count': max(total_count - confirmed_count, 0),
        'is_open': is_open
    })

@app.route('/api/register', methods=['POST'])
def register():
    data           = request.get_json()
    name           = data.get('name', '').strip()
    codename       = data.get('codename', '').strip()
    age            = data.get('age', '').strip()
    course_year    = data.get('course_year', '').strip()
    contact_number = data.get('contact_number', '').strip()
    email          = data.get('email', '').strip()

    if not all([name, age, course_year, contact_number, email]):
        return jsonify({'success': False, 'message': 'All fields are required.'})

    conn = get_db()

    # Hard guard: stop registrations once confirmed seats hit the cap
    try:
        current_confirmed = conn.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE confirmed = 1"
        ).fetchone()['c']
    except Exception as e:
        print(f"DB error while checking cap: {e}")
        conn.close()
        return jsonify({'success': False, 'message': 'Database error. Please try again.'})

    if current_confirmed >= MAX_PARTICIPANTS:
        conn.close()
        return jsonify({'success': False, 'message': 'Registration is closed. Slots are full.'})

    token = secrets.token_urlsafe(32)

    try:
        conn.execute("""
            INSERT INTO registrations (name, codename, age, course_year, contact_number, email, token)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, codename or None, age, course_year, contact_number, email, token))
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
            SELECT name, codename, course_year FROM registrations
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
            SELECT id, name, codename, age, course_year, contact_number,
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
#  DELETE REGISTRATION  —  removes a registration entry
# ------------------------------------------------------------
@app.route('/admin/delete/<int:reg_id>', methods=['POST'])
@login_required
def admin_delete_registration(reg_id):
    conn = get_db()
    try:
        # Check if the registration exists
        row = conn.execute(
            "SELECT id, name, email FROM registrations WHERE id = ?",
            (reg_id,)
        ).fetchone()

        if row is None:
            conn.close()
            flash('Registration not found.')
            return redirect(url_for('admin_dashboard'))

        # Delete the registration
        conn.execute("DELETE FROM registrations WHERE id = ?", (reg_id,))
        conn.commit()
    except Exception as e:
        print(f"DB error while deleting registration: {e}")
        conn.close()
        flash('Error deleting registration. Please try again.')
        return redirect(url_for('admin_dashboard'))
    else:
        conn.close()
        flash(f'Successfully deleted registration for "{row["name"]}" ({row["email"]}).')
    
    return redirect(url_for('admin_dashboard'))


# ------------------------------------------------------------
#  RESEND CONFIRMATION EMAIL  —  public API endpoint
#  SECURITY: Only resends to unconfirmed emails, rate limited
# ------------------------------------------------------------
@app.route('/api/resend-confirmation', methods=['POST'])
def resend_confirmation():
    """
    Resend confirmation email to a registered user.
    
    SECURITY MEASURES:
    - Only works for UNCONFIRMED emails (confirmed=0)
    - Uses the SAME token (doesn't generate new one)
    - Rate limited via session tracking
    - No database modification, only reads and sends email
    - Cannot be used to manipulate database
    """
    from flask import session
    import time
    
    data = request.get_json()
    email = data.get('email', '').strip().lower()

    if not email:
        return jsonify({'success': False, 'message': 'Email is required.'})
    
    # Validate email format
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return jsonify({
            'success': False,
            'message': 'Invalid email format.'
        })

    conn = get_db()
    try:
        # Find the registration by email (case-insensitive)
        row = conn.execute("""
            SELECT id, name, email, token, confirmed, registered_at
            FROM registrations
            WHERE LOWER(email) = ?
        """, (email,)).fetchone()

        # Email not found - don't reveal if email exists or not for security
        if row is None:
            conn.close()
            return jsonify({
                'success': False,
                'emailNotFound': True,
                'message': 'Email not found in our database. You may have used a different email or it was misspelled during registration.'
            })

        # SECURITY: Already confirmed - BLOCK resend
        if row['confirmed'] == 1:
            conn.close()
            print(f"[SECURITY] Blocked resend attempt for already-confirmed email: {email}")
            return jsonify({
                'success': False,
                'alreadyConfirmed': True,
                'message': 'This email is already confirmed! Your name should be showing on the participants list.'
            })

        # Rate limiting: Check if user requested recently (last 5 minutes)
        last_resend = session.get(f'resend_{email}')
        if last_resend and (time.time() - last_resend) < 300:  # 5 minutes
            conn.close()
            wait_time = int((300 - (time.time() - last_resend)) / 60)
            return jsonify({
                'success': False,
                'rateLimited': True,
                'message': f'Please wait {wait_time} minute(s) before requesting another email. This prevents spam.'
            })

        # SECURITY: Only resend if email is NOT confirmed (double-check)
        # This is the critical security check - cannot manipulate database
        if row['confirmed'] != 0:
            conn.close()
            print(f"[SECURITY] Blocked resend for email with non-zero confirmed status: {email}")
            return jsonify({
                'success': False,
                'message': 'Cannot resend confirmation for this email.'
            })

        # Send the confirmation email with the ORIGINAL token
        # This does NOT change the database, only sends email
        email_sent = send_confirmation_email(row['email'], row['name'], row['token'])

        if email_sent:
            # Record the resend timestamp for rate limiting
            session[f'resend_{email}'] = time.time()
            session.modified = True
            
            print(f"[SUCCESS] Resent confirmation email to: {email}")
            conn.close()
            return jsonify({
                'success': True,
                'message': 'Confirmation email resent successfully! Please check your inbox and spam folder.'
            })
        else:
            conn.close()
            print(f"[ERROR] Failed to send email to: {email}")
            return jsonify({
                'success': False,
                'message': 'Failed to send email. Please contact the organizers directly.'
            })

    except Exception as e:
        print(f"[ERROR] DB error while resending confirmation: {e}")
        conn.close()
        return jsonify({
            'success': False,
            'message': 'Database error. Please try again later.'
        })


# Helper function to escape HTML
def esc(text):
    """Escape HTML special characters to prevent XSS"""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')


# ------------------------------------------------------------
#  EXPORT CSV  —  downloads all registrations as a .csv file
# ------------------------------------------------------------
@app.route('/admin/export')
@login_required
def admin_export():
    export_format = request.args.get('format', 'csv').lower()
    fields_key    = request.args.get('fields', 'all').lower()

    # Field presets
    presets = {
        'all':       ['name', 'codename', 'age', 'course_year', 'contact_number', 'email', 'status', 'registered_at'],
        'all_name':  ['name'],
        'all_codename': ['codename'],
        'all_email': ['email'],
        'all_contact': ['contact_number'],
    }
    columns = presets.get(fields_key, presets['all'])

    conn = get_db()
    try:
        # Get all registration data
        rows = conn.execute("""
            SELECT name, codename, age, course_year, contact_number, email,
                   CASE WHEN confirmed = 1 THEN 'Confirmed' ELSE 'Pending' END AS status,
                   registered_at
            FROM registrations
            ORDER BY registered_at ASC
        """).fetchall()
        
        # Special handling for codename export
        codename_rows = None
        if fields_key == 'all_codename':
            codename_rows = conn.execute("""
                SELECT codename, name, course_year
                FROM registrations
                WHERE codename IS NOT NULL AND codename != ''
                ORDER BY registered_at ASC
            """).fetchall()

    except Exception as e:
        print(f"DB error: {e}")
        conn.close()
        return "Error generating export.", 500
    else:
        conn.close()

    # Build export
    if export_format == 'html':
        from flask import make_response

        # Special design for codename export (matches participants.html)
        if fields_key == 'all_codename' and codename_rows:
            codename_items = []
            for i, row in enumerate(codename_rows, 1):
                codename_items.append(f"""
        <div class="codename-card">
            <div class="card-num">{str(i).zfill(2)}</div>
            <div class="card-codename">{esc(row['codename'])}</div>
        </div>""")

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hack4Gov - Codename List</title>
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #0a0a0a;
            font-family: 'Rajdhani', sans-serif;
            color: #f0f0f0;
            min-height: 100vh;
        }}
        body::before {{
            content: '';
            position: fixed;
            inset: 0;
            background-image:
                linear-gradient(#8B1A1A 1px, transparent 1px),
                linear-gradient(90deg, #8B1A1A 1px, transparent 1px);
            background-size: 40px 40px;
            opacity: 0.06;
            pointer-events: none;
        }}
        .top-bar {{
            background: #8B1A1A;
            padding: 10px 24px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 2px solid #c0392b;
            position: relative;
            z-index: 2;
        }}
        .dot {{
            width: 10px; height: 10px;
            border-radius: 50%;
            background: #ff4444;
            animation: blink 1.2s infinite;
        }}
        @keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0.3}} }}
        .top-bar-text {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 12px;
            color: #ffd0d0;
            letter-spacing: 2px;
        }}
        .page-header {{
            text-align: center;
            padding: 28px 24px 16px;
            position: relative;
            z-index: 2;
        }}
        .page-title {{
            font-size: 13px;
            font-family: 'Share Tech Mono', monospace;
            color: #8B1A1A;
            letter-spacing: 4px;
            text-transform: uppercase;
            margin-bottom: 6px;
        }}
        .page-h1 {{
            font-size: 32px;
            font-weight: 700;
            letter-spacing: 4px;
            color: #fff;
            text-transform: uppercase;
        }}
        .stats-row {{
            display: flex;
            justify-content: center;
            gap: 16px;
            margin: 16px auto;
            max-width: 600px;
            position: relative;
            z-index: 2;
            padding: 0 24px;
        }}
        .stat-box {{
            flex: 1;
            background: #111;
            border: 1px solid #2a0a0a;
            border-top: 2px solid #8B1A1A;
            border-radius: 2px;
            padding: 12px;
            text-align: center;
        }}
        .stat-num {{
            font-size: 28px;
            font-weight: 700;
            color: #c0392b;
            font-family: 'Share Tech Mono', monospace;
        }}
        .stat-lbl {{
            font-size: 11px;
            color: #555;
            letter-spacing: 2px;
            font-family: 'Share Tech Mono', monospace;
            text-transform: uppercase;
        }}
        .codename-list {{
            max-width: 700px;
            margin: 0 auto 40px;
            padding: 0 24px;
            position: relative;
            z-index: 2;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 12px;
        }}
        .codename-card {{
            background: #0d0d0d;
            border: 1px solid #1a0505;
            border-radius: 4px;
            padding: 16px;
            display: flex;
            align-items: center;
            gap: 14px;
            transition: border-color 0.15s, background 0.15s;
        }}
        .codename-card:hover {{
            background: #120505;
            border-color: #3a0a0a;
        }}
        .card-num {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 14px;
            color: #8B1A1A;
            min-width: 28px;
        }}
        .card-codename {{
            font-size: 18px;
            font-weight: 700;
            color: #c0392b;
            font-family: 'Share Tech Mono', monospace;
            letter-spacing: 2px;
            flex: 1;
        }}
        .card-name {{
            font-size: 12px;
            color: #555;
            font-family: 'Share Tech Mono', monospace;
        }}
        .footer {{
            position: relative;
            z-index: 2;
            padding: 14px 20px 24px;
            text-align: center;
            font-family: 'Share Tech Mono', monospace;
            font-size: 11px;
            color: #555;
            letter-spacing: 1.5px;
        }}
        @media (max-width: 640px) {{
            .top-bar {{ padding: 10px 14px; }}
            .page-header {{ padding: 22px 14px 10px; }}
            .page-title {{ font-size: 11px; }}
            .page-h1 {{ font-size: 24px; }}
            .codename-list {{ grid-template-columns: 1fr; padding: 0 14px; }}
        }}
    </style>
</head>
<body>
    <div class="top-bar">
        <div class="dot"></div>
        <span class="top-bar-text">HACK4GOV // CODENAME EXPORT</span>
    </div>

    <div class="page-header">
        <div class="page-title">hack4gov / codename directory</div>
        <div class="page-h1">All Codenames</div>
    </div>

    <div class="stats-row">
        <div class="stat-box">
            <div class="stat-num">{len(codename_items)}</div>
            <div class="stat-lbl">Total Codenames</div>
        </div>
        <div class="stat-box">
            <div class="stat-num" style="font-size:18px;padding-top:4px;">APR 20</div>
            <div class="stat-lbl">Event Start</div>
        </div>
    </div>

    <div class="codename-list">
        {''.join(codename_items) if codename_items else '<div class="stat-box" style="grid-column:1/-1;"><div class="stat-lbl" style="padding:40px;">NO CODENAMES YET</div></div>'}
    </div>

    <div class="footer">
        Exported from Hack4Gov Admin Panel · {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>
</body>
</html>"""
        else:
            # Generic HTML table for other exports
            header_cells = ''.join(f'<th>{h.replace("_"," ").title()}</th>' for h in columns)
            body_rows = []
            for row in rows:
                body_cells = []
                for col in columns:
                    val = row[col] if col != 'codename' else (row[col] or '')
                    body_cells.append(f'<td>{esc(str(val))}</td>')
                body_rows.append('<tr>' + ''.join(body_cells) + '</tr>')
            html = f"""<!DOCTYPE html>
            <html><head><meta charset='UTF-8'><title>Hack4Gov Export</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ background: #0a0a0a; font-family: monospace; color: #f0f0f0; padding: 20px; }}
                h3 {{ color: #8B1A1A; margin: 20px 0; font-family: monospace; }}
                table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
                th, td {{ border: 1px solid #2a0a0a; padding: 10px 14px; text-align: left; }}
                th {{ background: #8B1A1A; color: #fff; font-size: 11px; letter-spacing: 2px; }}
                td {{ background: #0d0d0d; font-size: 13px; }}
                tr:hover td {{ background: #120505; }}
            </style></head><body>
            <h3>Hack4Gov Export ({fields_key.replace('_', ' ').title()})</h3>
            <table><thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>
            </body></html>"""
        
        resp = make_response(html)
        resp.headers['Content-Type'] = 'text/html'
        resp.headers['Content-Disposition'] = f'attachment; filename=hack4gov_{fields_key}.html'
        return resp

    # default CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([c.replace('_',' ').title() for c in columns])
    for row in rows:
        writer.writerow([(row[col] if col != 'codename' else (row[col] or '')) for col in columns])

    csv_data = output.getvalue()
    output.close()
    from flask import make_response
    response = make_response(csv_data)
    response.headers['Content-Type']        = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=hack4gov_{fields_key}.csv'
    return response


# ============================================================
#  ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500


# ============================================================
#  INITIALIZE DB + RUN
# ============================================================
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True)
    
