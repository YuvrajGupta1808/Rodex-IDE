def read_file(path):
    # BUG: file opened without 'with', won't be closed on exception
    f = open(path, 'r')
    data = f.read()
    return data  # f.close() never called

def write_log(message, log_path):
    # BUG: file handle leaked if an exception occurs before close
    log = open(log_path, 'a')
    log.write(message + '\n')
    log.close()

def process_db():
    import sqlite3
    # BUG: connection never closed
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE t (x INT)")
    return cursor.fetchall()
