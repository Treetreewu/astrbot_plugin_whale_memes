"""Compatible with v1 notes; atomic quota and deletion barriers survive restarts."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .config import Config
from .evidence import normalize, text


def scope_key(bot, persona, session):
    parts = [text(v, 512, "scope") for v in (bot, persona, session)]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, path, config=None, clock=time.time):
        self.path, self.config, self.clock = Path(path), config or Config(), clock

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS notes (
                scope TEXT NOT NULL, term_key TEXT NOT NULL, term TEXT NOT NULL, note TEXT NOT NULL,
                checked_at REAL NOT NULL, used_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scope,term_key))""")
            db.execute("CREATE TABLE IF NOT EXISTS generations (scope TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (scope TEXT NOT NULL, started REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS attempts_scope_time ON attempts(scope,started)")
            yield db

    def lookup(self, scope, term):
        name, now = normalize(term), self.clock()
        with self.db() as db:
            found = []
            for stored, title, encoded, checked, hits in db.execute(
                    "SELECT term_key,term,note,checked_at,hits FROM notes WHERE scope=?", (scope,)):
                note = json.loads(encoded)
                names = [stored, *(normalize(a) for a in note.get("aliases", []))]
                rank = 3 if name == stored else 2 if name in names else 1 if any(name in n for n in names) else 0
                if rank:
                    stale = now - checked >= self.config.ttl_days * 86400
                    trusted = note.get("verification") == "source_quote_matched"
                    found.append((rank, hits, checked, stored, dict(note, term=title, checked_at=checked,
                        stale=stale, needs_verification=stale or not trusted or rank < 2)))
            found.sort(key=lambda r: r[:3], reverse=True)
            for row in found[:5]:
                db.execute("UPDATE notes SET hits=hits+1,used_at=? WHERE scope=? AND term_key=?", (now, scope, row[3]))
        return [r[4] for r in found[:5]]

    def generation(self, scope):
        with self.db() as db:
            row = db.execute("SELECT version FROM generations WHERE scope=?", (scope,)).fetchone()
            return row[0] if row else 0

    def reserve(self, scope, expected):
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM generations WHERE scope=?", (scope,)).fetchone()
            if (row[0] if row else 0) != expected:
                return -1
            db.execute("DELETE FROM attempts WHERE started<=?", (now - 3600,))
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE scope=?", (scope,)).fetchone()[0]
            if count >= self.config.learning_per_hour:
                return None
            db.execute("INSERT INTO attempts VALUES (?,?)", (scope, now))
            db.execute("INSERT OR IGNORE INTO generations VALUES (?,0)", (scope,))
            return db.execute("SELECT version FROM generations WHERE scope=?", (scope,)).fetchone()[0]

    def save(self, scope, term, note, expected):
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT version FROM generations WHERE scope=?", (scope,)).fetchone()
            if current is None or current[0] != expected:
                return False
            # Resolve aliases inside the same transaction as the generation
            # check and upsert. A refresh must replace the canonical record.
            key = normalize(term)
            rows = list(db.execute("SELECT term_key,term,note FROM notes WHERE scope=?", (scope,)))
            exact = next((row for row in rows if row[0] == key), None)
            aliases = [row for row in rows if key in [normalize(a) for a in json.loads(row[2]).get("aliases", [])]]
            if exact is None and len(aliases) > 1:
                raise ValueError("Ambiguous alias; refresh a canonical term")
            target = exact or (aliases[0] if aliases else None)
            if target:
                old = json.loads(target[2])
                retained = ([term] if key != target[0] else []) + note.get("aliases", []) + old.get("aliases", [])
                note = {**note, "aliases": list(dict.fromkeys(retained))[:5]}
                term = target[1]
            db.execute("""INSERT INTO notes(scope,term_key,term,note,checked_at,used_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(scope,term_key) DO UPDATE SET term=excluded.term,note=excluded.note,
                checked_at=excluded.checked_at,used_at=excluded.used_at""",
                (scope, normalize(term), term, json.dumps(note, ensure_ascii=False), now, now))
            # Invalidate any other in-flight interpretation of this scope.
            db.execute("UPDATE generations SET version=version+1 WHERE scope=?", (scope,))
            db.execute("""DELETE FROM notes WHERE rowid IN (SELECT rowid FROM notes WHERE scope=?
                ORDER BY used_at DESC,rowid DESC LIMIT -1 OFFSET ?)""", (scope, self.config.max_notes_per_scope))
            db.execute("""DELETE FROM notes WHERE rowid IN (SELECT rowid FROM notes
                ORDER BY used_at DESC,rowid DESC LIMIT -1 OFFSET ?)""", (self.config.max_notes_total,))
        return True

    def forget(self, scope, term):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO generations VALUES (?,1)
                ON CONFLICT(scope) DO UPDATE SET version=version+1""", (scope,))
            return db.execute("DELETE FROM notes WHERE scope=? AND term_key=?", (scope, normalize(term))).rowcount
