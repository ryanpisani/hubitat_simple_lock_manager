import requests
from datetime import datetime, timedelta

# Flask Server Information
FLASK_BASE_URL = "http://localhost:5002"  # Adjust if hosted remotely
LOGIN_URL = f"{FLASK_BASE_URL}/login"
ADD_CODE_URL = f"{FLASK_BASE_URL}/add"

# User Credentials for Authentication
USERNAME = "admin"
PASSWORD = "adminpass"

# Lock Code Information
lock_user = "GuestUser"
enable_time = (datetime.now()).strftime("%Y-%m-%dT%H:%M")  # Enable 5 min from now
expire_time = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")  # Expire in 1 hour

# Start a session to persist login cookies
session = requests.Session()

# Step 1: Login to Flask-Login
login_data = {"username": USERNAME, "password": PASSWORD}
response = session.post(LOGIN_URL, data=login_data)

if response.status_code == 200:
    print("Login successful!")

    # Step 2: Send a POST request to add a lock code
    payload = {
        "user": lock_user,
        "enable_time": enable_time,
        "expire_time": expire_time
    }
    add_response = session.post(ADD_CODE_URL, data=payload)

    if add_response.status_code == 200:
        print("Lock code successfully scheduled!")
    else:
        print(f"Failed to schedule lock code: {add_response.text}")
else:
    print("Login failed:", response.text)
