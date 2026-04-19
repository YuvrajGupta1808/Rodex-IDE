import threading

counter = 0

def increment():
    global counter
    # BUG: race condition — read-modify-write without lock
    temp = counter
    temp += 1
    counter = temp

def run_threads():
    threads = [threading.Thread(target=increment) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return counter  # likely != 100 due to race
