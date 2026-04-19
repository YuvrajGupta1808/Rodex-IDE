def get_user_email(user_id):
    user = find_user(user_id)
    # BUG: user may be None, accessing .email will raise AttributeError
    return user.email

def find_user(user_id):
    users = {1: type('User', (), {'email': 'a@b.com'})()}
    return users.get(user_id)  # returns None if not found

def process_config(config):
    # BUG: config["db"] may not exist, and .host may be None
    db = config.get("db")
    return db.host  # AttributeError if db is None
