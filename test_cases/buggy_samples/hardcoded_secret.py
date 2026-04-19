import requests

# VULNERABILITY: hardcoded API key
API_KEY = "sk-live-abc123supersecretkey9876"
DATABASE_PASSWORD = "MyP@ssw0rd123!"

def call_api(endpoint):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    return requests.get(endpoint, headers=headers)

def connect_db():
    # VULNERABILITY: hardcoded credentials
    return {"host": "localhost", "password": "admin123", "user": "root"}
