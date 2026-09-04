"""Tests for the AST-based fix verification gate."""

from src.agents.fix_verification import pattern_occurrences, pattern_removed

SQLI_BEFORE = '''
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("app.db")
    return conn.execute(f"SELECT * FROM users WHERE id = {user_id}").fetchall()
'''

SQLI_AFTER = '''
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("app.db")
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchall()
'''

SWALLOW_BEFORE = '''
def run():
    try:
        risky()
    except:
        pass
'''

SWALLOW_AFTER = '''
import logging

def run():
    try:
        risky()
    except ValueError as exc:
        logging.error("failed: %s", exc)
        raise
'''


def test_detects_sql_injection_and_its_removal():
    assert pattern_occurrences(SQLI_BEFORE, "sql_injection") == 1
    assert pattern_occurrences(SQLI_AFTER, "sql_injection") == 0
    assert pattern_removed(SQLI_BEFORE, SQLI_AFTER, "sql_injection") is True


def test_no_op_fix_is_rejected():
    assert pattern_removed(SQLI_BEFORE, SQLI_BEFORE, "sql_injection") is False


def test_detects_error_swallowing():
    assert pattern_occurrences(SWALLOW_BEFORE, "error_swallowing") == 1
    assert pattern_removed(SWALLOW_BEFORE, SWALLOW_AFTER, "error_swallowing") is True


def test_command_injection_shell_true():
    before = 'import subprocess\nsubprocess.run(cmd, shell=True)\n'
    after = 'import subprocess\nsubprocess.run(["ls", target])\n'
    assert pattern_removed(before, after, "command_injection") is True


def test_unsafe_deserialization():
    before = 'import pickle\ndata = pickle.loads(blob)\n'
    after = 'import json\ndata = json.loads(blob)\n'
    assert pattern_removed(before, after, "unsafe_deserialization") is True


def test_unknown_category_yields_no_opinion():
    assert pattern_removed(SQLI_BEFORE, SQLI_AFTER, "xss") is None


def test_unparseable_source_yields_no_opinion():
    assert pattern_removed(SQLI_BEFORE, "def broken(", "sql_injection") is None


def test_absent_pattern_yields_no_opinion():
    clean = 'x = 1\n'
    assert pattern_removed(clean, clean, "sql_injection") is None
