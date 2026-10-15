"""Persistent pause requests shared by GUI and CLI workers in one archive."""


def _connection(store):
    connection = store._connect()
    connection.execute("CREATE TABLE IF NOT EXISTS batch_control ("
                       "batch_id TEXT PRIMARY KEY, pause_requested INTEGER NOT NULL DEFAULT 0, "
                       "worker_registered INTEGER NOT NULL DEFAULT 0)")
    return connection


def request_pause(store, batch_id):
    _connection(store)
    with store.transaction() as connection:
        connection.execute("INSERT INTO batch_control (batch_id, pause_requested) VALUES (?, 1) "
                           "ON CONFLICT(batch_id) DO UPDATE SET pause_requested=1", (batch_id,))


def register_worker(store, batch_id, *, clear=False):
    _connection(store)
    with store.transaction() as connection:
        connection.execute("INSERT INTO batch_control VALUES (?, 0, 1) "
                           "ON CONFLICT(batch_id) DO UPDATE SET worker_registered=1, "
                           "pause_requested=CASE WHEN ? THEN 0 ELSE pause_requested END", (batch_id, clear))


def pause_requested(store, batch_id):
    row = _connection(store).execute("SELECT pause_requested FROM batch_control WHERE batch_id=?",
                                     (batch_id,)).fetchone()
    return bool(row and row[0])


def supports_pause(store, batch_id):
    row = _connection(store).execute("SELECT worker_registered FROM batch_control WHERE batch_id=?",
                                     (batch_id,)).fetchone()
    return bool(row and row[0])
