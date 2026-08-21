"""Patch vault.py for sign-on-write. Idempotent; refuses if shape is unexpected."""

import pathlib
import sys

TARGET = pathlib.Path(sys.argv[1])
src = TARGET.read_text(encoding="utf-8")

if "vault_diary" in src:
    print("  already patched — nothing to do")
    sys.exit(0)

# ---- 1. import + schema bootstrap ---------------------------------------
OLD_APP = 'app = FastAPI(title="The Mess — Memory Vault")'
NEW_APP = '''app = FastAPI(title="The Mess — Memory Vault")

# kin-diary v1 sign-on-write (2026-08-21). Optional by construction: if the
# module or its crypto dependency is missing, the vault still runs and simply
# writes unsigned rows, exactly as it did before.
try:
    import vault_diary
except Exception as _e:  # noqa: BLE001
    vault_diary = None
    print(f"[vault_diary unavailable, writing unsigned: {_e}]")'''
assert OLD_APP in src, "FastAPI app line not found"
src = src.replace(OLD_APP, NEW_APP, 1)

# ---- 2. run the migration once at import --------------------------------
OLD_DB = 'DB_PATH = os.path.expanduser("~/themess/themess.db")'
# Env override so the service can be pointed at a copy for testing without
# editing the file. Default is unchanged, so production behaviour is identical.
NEW_DB = ('DB_PATH = os.environ.get("VAULT_DB", '
          'os.path.expanduser("~/themess/themess.db"))') + '''


def _init_diary_schema():
    """Add the diary columns once at startup.

    ALTER TABLE takes an exclusive lock, so this runs at import — before the
    server accepts traffic — rather than lazily on a request path where it
    could stall a live write.
    """
    if vault_diary is None:
        return
    try:
        c = sqlite3.connect(DB_PATH)
        added = vault_diary.ensure_schema(c)
        c.close()
        if added:
            print(f"[vault_diary: added columns {added}]")
    except Exception as e:  # noqa: BLE001
        print(f"[vault_diary: schema init failed, writing unsigned: {e}]")'''
assert OLD_DB in src, "DB_PATH line not found"
src = src.replace(OLD_DB, NEW_DB, 1)

# The call must follow the definition: `app` is declared earlier in the file
# than DB_PATH, so anchoring the bootstrap to the app block would call the
# function before it exists.
src = src.replace(NEW_DB, NEW_DB + "\n\n\n_init_diary_schema()", 1)

# ---- 3. sign on write ---------------------------------------------------
OLD_REMEMBER = '''@app.post("/remember")
def remember(memory: Memory):
    conn = get_db()
    conn.execute("""
        INSERT INTO memories (author, timestamp, layer, content, tags, visibility,
                              source, domain, tier, salience)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory.author,
        datetime.now().isoformat(),
        memory.layer,
        memory.content,
        memory.tags,
        memory.visibility,
        memory.source,
        memory.domain,
        memory.tier,
        memory.salience
    ))
    conn.commit()
    conn.close()
    return {"status": "written to the walls"}'''

NEW_REMEMBER = '''@app.post("/remember")
def remember(memory: Memory):
    # One timestamp, used for both the row and the signature. Deriving it
    # twice is how the stored value and the signed value drift apart.
    ts = datetime.now().isoformat()

    # Belt and braces. sign_row already swallows everything internally, but
    # rule 2 says a memory must never be lost to a signing problem, and that
    # promise should not depend on one blanket except in another module —
    # a version skew or a broken import would otherwise 500 the write away.
    sha = key_id = signature = None
    if vault_diary is not None:
        try:
            sha, key_id, signature = vault_diary.sign_row(
                memory.author, ts, memory.layer, memory.content,
                memory.tags, memory.source, memory.domain)
        except Exception as e:  # noqa: BLE001
            print(f"[vault_diary: sign_row raised, storing unsigned: {e}]")
            sha = key_id = signature = None

    conn = get_db()
    conn.execute("""
        INSERT INTO memories (author, timestamp, layer, content, tags, visibility,
                              source, domain, tier, salience,
                              content_sha256, key_id, signature)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory.author,
        ts,
        memory.layer,
        memory.content,
        memory.tags,
        memory.visibility,
        memory.source,
        memory.domain,
        memory.tier,
        memory.salience,
        sha,
        key_id,
        signature
    ))
    conn.commit()
    conn.close()
    return {"status": "written to the walls", "signed": signature is not None}


@app.get("/diary/health")
def diary_health():
    """Is signing actually working right now?

    sign_row swallows every exception on purpose, so without this a persistent
    break writes NULL signatures forever while every request still returns 200.
    """
    if vault_diary is None:
        return {"ok": False, "error": "vault_diary not loaded"}
    return vault_diary.health()'''

assert OLD_REMEMBER in src, "/remember body did not match expected shape"
src = src.replace(OLD_REMEMBER, NEW_REMEMBER, 1)

TARGET.write_text(src, encoding="utf-8")
print("  patched:", TARGET)
