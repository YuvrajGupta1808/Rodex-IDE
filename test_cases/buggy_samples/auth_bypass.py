def check_admin(user):
    # VULNERABILITY: string comparison allows bypass with truthy string
    if user.get("role") == "admin" or user.get("is_admin"):
        return True
    return False

def login(username, password):
    # VULNERABILITY: password compared with == allows timing attack
    stored_password = get_stored_password(username)
    if stored_password == password:
        return {"authenticated": True, "user": username}
    return None

def get_stored_password(username):
    passwords = {"admin": "secret123"}
    return passwords.get(username, "")

def protected_endpoint(request):
    # VULNERABILITY: auth check can be bypassed if header is missing
    token = request.get("X-Auth-Token")
    if token:
        return process_request(request)
    # Missing: return 401 Unauthorized

def process_request(req):
    return {"status": "ok"}
