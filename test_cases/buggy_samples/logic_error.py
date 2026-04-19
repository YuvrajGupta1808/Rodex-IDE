def find_max(numbers):
    # BUG: off-by-one — loop misses last element
    max_val = numbers[0]
    for i in range(len(numbers) - 1):
        if numbers[i] > max_val:
            max_val = numbers[i]
    return max_val

def is_valid_age(age):
    # BUG: inverted condition — rejects valid ages
    if not (0 < age < 120):
        return True
    return False

def paginate(items, page, page_size):
    # BUG: off-by-one in slice — misses last item on final page
    start = (page - 1) * page_size
    end = start + page_size - 1
    return items[start:end]
