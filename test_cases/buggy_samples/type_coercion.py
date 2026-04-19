def add_values(a, b):
    # BUG: if a="3" and b=2, returns "32" instead of 5
    return a + b

def calculate_discount(price, discount_pct):
    # BUG: discount_pct from form is string, multiplication gives wrong result
    return price - price * discount_pct / 100

def parse_config(data):
    # BUG: comparing string "True" to bool True always False
    if data.get("enabled") == True:
        return "enabled"
    return "disabled"
