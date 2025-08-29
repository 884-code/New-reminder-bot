# ==== PRELUDE_STUBS (NameError対策) ====
import logging, sqlite3
logger = logging.getLogger(__name__)

# init_database が無ければスタブを用意
try:
    init_database
except NameError:
    def init_database():
        try: logger.info("[init] スタブ実行（本体未定義）")
        except Exception: pass
        # DBファイルを触っておくだけ（本体があればそちらが使われる）
        conn = sqlite3.connect('reminder_bot.db'); conn.close()

# setup_roles が無ければスタブを用意
try:
    setup_roles
except NameError:
    import discord as _d
    async def setup_roles(guild: _d.Guild):
        try: logger.info(f"[setup_roles] スタブ実行（{getattr(guild,'name','?')}）")
        except Exception: pass
        return None, None
# ==== /PRELUDE_STUBS ====
