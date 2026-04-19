import json

def parse_json(data):
    try:
        return json.loads(data)
    except:
        # BUG: swallows ALL exceptions silently — caller never knows it failed
        pass

def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        pass  # BUG: returns None silently instead of raising or returning sentinel

def connect_service(url):
    try:
        import httpx
        return httpx.get(url)
    except Exception as e:
        # BUG: logs nothing, returns None, caller has no idea what failed
        return None
