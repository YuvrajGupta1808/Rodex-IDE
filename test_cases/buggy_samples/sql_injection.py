import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # VULNERABILITY: user input directly concatenated into SQL
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    return cursor.fetchone()

def search_users(username):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # VULNERABILITY: string format in SQL query
    query = "SELECT * FROM users WHERE username = '%s'" % username
    cursor.execute(query)
    return cursor.fetchall()
