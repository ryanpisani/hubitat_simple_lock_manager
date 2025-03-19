import random
import time
import requests
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from flask_apscheduler import APScheduler

# Flask App Setup
app = Flask(__name__)
app.secret_key = "supersecretkey"
bcrypt = Bcrypt(app)

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Scheduler Setup
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# Dummy User Database
users = {"admin": bcrypt.generate_password_hash("adminpass").decode("utf-8")}


class User(UserMixin):
    def __init__(self, username):
        self.id = username


@login_manager.user_loader
def load_user(user_id):
    if user_id in users:
        return User(user_id)
    return None


# Hubitat API Settings
HUBITAT_BASE_URL = "http://your-hubitat-ip/apps/api/your-app-id"
ACCESS_TOKEN = "your-access-token"
LOCK_DEVICE_ID = "your-lock-device-id"

# Lock Code Management
LOCK_CODE_FILE = "lock_codes.json"
FAILED_CODES_FILE = "failed_codes.json"

# Generate a random 6-digit lock code
def generate_lock_code():
    return str(random.randint(1000, 9999))  # Ensures a 4-digit code

# Convert datetime to Unix timestamp
def to_unix_timestamp(dt):
    return int(time.mktime(dt.timetuple()))

# Function to get existing lock codes from JSON and Hubitat API
def get_existing_lock_codes():
    try:
        with open(LOCK_CODE_FILE, "r") as f:
            lock_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        lock_data = {}

    api_url = f"{HUBITAT_BASE_URL}/devices/{LOCK_DEVICE_ID}?access_token={ACCESS_TOKEN}"
    response = requests.get(api_url)

    if response.status_code == 200:
        data = response.json()
        for attr in data["attributes"]:
            if attr["name"] == "lockCodes":
                try:
                    lock_codes = json.loads(attr["currentValue"])
                    for slot, info in lock_codes.items():
                        if slot in lock_data:
                            lock_data[slot]["user"] = info.get("name", "Unknown")
                        else:
                            lock_data[slot] = {
                                "user": info.get("name", "Unknown"),
                                "enable_at": lock_data.get(slot, {}).get("enable_at", "Unknown"),
                                "expires_at": lock_data.get(slot, {}).get("expires_at", "Unknown")
                            }
                    return lock_data
                except json.JSONDecodeError:
                    print("Error parsing lock codes JSON")
    return lock_data

# Function to find the next available slot
def find_next_available_slot():
    existing_codes = get_existing_lock_codes()
    used_slots = {int(slot) for slot in existing_codes.keys()}

    for slot in range(1, 31):  # Assuming max 30 slots
        if slot not in used_slots:
            return slot
    return None

# Function to enable a lock code at a scheduled time
def enable_lock_code(lock_slot, lock_code, lock_user):
    existing_codes = get_existing_lock_codes()

    # Check if the code is already in the existing lock codes
    if str(lock_slot) in existing_codes.keys():
        print(f"Lock code for slot {lock_slot} already exists. Skipping API request.")
        return

    api_url = f"{HUBITAT_BASE_URL}/devices/{LOCK_DEVICE_ID}/setCode/{lock_slot},{lock_code},{lock_user}?access_token={ACCESS_TOKEN}"
    response = requests.get(api_url)
    
    # Fetch updated lock codes from API to verify if the new code was added
    verify_url = f"{HUBITAT_BASE_URL}/devices/{LOCK_DEVICE_ID}?access_token={ACCESS_TOKEN}"
    verify_response = requests.get(verify_url)

    try:
        verify_data = verify_response.json()
        lock_codes_json = next((attr['currentValue'] for attr in verify_data['attributes'] if attr['name'] == 'lockCodes'), '{}')
        lock_codes = json.loads(lock_codes_json)
    except (json.JSONDecodeError, KeyError, TypeError):
        lock_codes = {}

    print(lock_codes)
    print(lock_codes_json)
    # Check if the slot was successfully added
    if str(lock_slot) in lock_codes.keys():
        print(f"Lock code {lock_code} enabled for {lock_user} in slot {lock_slot}")
    else:
        error_message = f"Failed to enable lock code {lock_code} for {lock_user} in slot {lock_slot}" \
                        f" - Code not found in API response."
        print(error_message)
        log_failed_code(lock_slot, lock_code, lock_user, error_message)

# Function to remove a scheduled job
def remove_scheduled_jobs(lock_slot):
    job_ids = [f"enable_{lock_slot}", f"remove_{lock_slot}"]
    for job_id in job_ids:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            print(f"Removed scheduled job: {job_id}")

# Function to log failed lock code attempts
def log_failed_code(lock_slot, lock_code, lock_user, error_message):
    try:
        with open(FAILED_CODES_FILE, "r") as f:
            failed_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        failed_data = {}

    failed_data[str(lock_slot)] = {
        "code": lock_code,
        "user": lock_user,
        "error": error_message,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(FAILED_CODES_FILE, "w") as f:
        json.dump(failed_data, f, indent=4)

# Function to get failed lock codes
def get_failed_lock_codes():
    try:
        with open(FAILED_CODES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

# Function to remove expired lock codes and clean up JSON file
def remove_expired_code(lock_slot):
    api_url = f"{HUBITAT_BASE_URL}/devices/{LOCK_DEVICE_ID}/deleteCode/{lock_slot}?access_token={ACCESS_TOKEN}"
    response = requests.get(api_url)

    if response.status_code == 200:
        print(f"Lock code removed from slot {lock_slot}")
        
        # Remove from lock_codes.json if it's no longer in the system
        try:
            with open(LOCK_CODE_FILE, "r") as f:
                lock_data = json.load(f)
            
            if str(lock_slot) in lock_data:
                del lock_data[str(lock_slot)]
                with open(LOCK_CODE_FILE, "w") as f:
                    json.dump(lock_data, f, indent=4)
                print(f"Lock code entry removed from JSON file for slot {lock_slot}")
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    else:
        print(f"Failed to remove lock code: {response.text}")

# Function to store lock codes and schedule activation/expiration
def save_lock_code(lock_slot, lock_code, lock_user, enable_time, expire_time):
    try:
        with open(LOCK_CODE_FILE, "r") as f:
            lock_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        lock_data = {}

    lock_data[str(lock_slot)] = {
        "code": lock_code,
        "user": lock_user,
        "enable_at": enable_time.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": expire_time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(LOCK_CODE_FILE, "w") as f:
        json.dump(lock_data, f, indent=4)

    # Schedule enabling of the lock code
    scheduler.add_job(
        id=f"enable_{lock_slot}",
        func=enable_lock_code,
        args=[lock_slot, lock_code, lock_user],
        trigger="date",
        run_date=enable_time
    )

    # Schedule expiration of the lock code
    scheduler.add_job(
        id=f"remove_{lock_slot}",
        func=remove_expired_code,
        args=[lock_slot],
        trigger="date",
        run_date=expire_time
    )

def audit_lock_codes():
    """
    Audits lock codes on application startup:
    - Removes expired lock codes.
    - Restores scheduled enablements.
    """
    try:
        with open(LOCK_CODE_FILE, "r") as f:
            lock_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No existing lock codes found.")
        return

    now = datetime.now()
    updated_data = {}

    for slot, details in lock_data.items():
        enable_time = datetime.strptime(details["enable_at"], "%Y-%m-%d %H:%M:%S")
        expire_time = datetime.strptime(details["expires_at"], "%Y-%m-%d %H:%M:%S")

        if expire_time <= now:
            print(f"Removing expired lock code in slot {slot}")
            remove_expired_code(slot)  # Remove from Hubitat
        else:
            updated_data[slot] = details  # Retain valid codes

            #Immediately enable the key if we are already in the active window
            if enable_time < now and expire_time > now:
                enable_lock_code(slot, details["code"], details["user"])

            # Reschedule enablement if it hasn't occurred yet
            if enable_time > now:
                scheduler.add_job(
                    id=f"enable_{slot}",
                    func=enable_lock_code,
                    args=[slot, details["code"], details["user"]],
                    trigger="date",
                    run_date=enable_time
                )
                print(f"Re-scheduled enablement for slot {slot} at {enable_time}")

            # Reschedule expiration if it hasn't occurred yet
            scheduler.add_job(
                id=f"remove_{slot}",
                func=remove_expired_code,
                args=[slot],
                trigger="date",
                run_date=expire_time
            )
            print(f"Re-scheduled expiration for slot {slot} at {expire_time}")

    # Save the cleaned-up lock code data
    with open(LOCK_CODE_FILE, "w") as f:
        json.dump(updated_data, f, indent=4)


# Function to remove a failed lock code from JSON file
def remove_failed_code(lock_slot):
    try:
        with open(FAILED_CODES_FILE, "r") as f:
            failed_data = json.load(f)
        
        if str(lock_slot) in failed_data:
            del failed_data[str(lock_slot)]
            with open(FAILED_CODES_FILE, "w") as f:
                json.dump(failed_data, f, indent=4)
            print(f"Removed failed lock code entry for slot {lock_slot}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
  
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username in users and bcrypt.check_password_hash(users[username], password):
            login_user(User(username))
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password", "danger")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    lock_codes = get_existing_lock_codes()
    failed_codes = get_failed_lock_codes()
    return render_template("index.html", lock_codes=lock_codes, failed_codes=failed_codes)

@app.route("/add", methods=["POST"])
@login_required
def add_code():
    lock_slot = find_next_available_slot()
    if lock_slot is None:
        flash("No available slots for new lock codes!", "danger")
        return redirect(url_for("index"))

    lock_code = generate_lock_code()
    lock_user = request.form["user"]

    enable_time_str = request.form["enable_time"]
    expire_time_str = request.form["expire_time"]

    enable_time = datetime.strptime(enable_time_str, "%Y-%m-%dT%H:%M")
    expire_time = datetime.strptime(expire_time_str, "%Y-%m-%dT%H:%M")

    save_lock_code(lock_slot, lock_code, lock_user, enable_time, expire_time)

    flash(f"Lock code {lock_code} scheduled for {lock_user} (Slot {lock_slot})", "success")
    return redirect(url_for("index"))

@app.route("/delete/<slot>")
@login_required
def delete_code_route(slot):
    remove_expired_code(slot)
    remove_scheduled_jobs(slot)
    remove_failed_code(slot)
    return redirect(url_for("index"))

@app.route("/scheduled-jobs")
@login_required
def scheduled_jobs():
    jobs = scheduler.get_jobs()
    jobs_list = [
        {"id": job.id, "next_run": str(job.next_run_time), "function": job.func_ref, "args": job.args}
        for job in jobs
    ]
    return render_template("jobs.html", jobs=jobs_list)


if __name__ == "__main__":
    audit_lock_codes()  # Restore scheduled jobs on startup
    app.run(debug=True, port=5002)
