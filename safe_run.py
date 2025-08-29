import os, asyncio, logging, types, importlib, discord
logging.basicConfig(level=logging.DEBUG)  # ← DEBUGに
log = logging.getLogger("safe_run")
logging.getLogger("discord").setLevel(logging.DEBUG)
logging.getLogger("discord.http").setLevel(logging.DEBUG)
logging.getLogger("discord.gateway").setLevel(logging.DEBUG)

# mybot をモジュールとして読み込む（mybot.py の bot.run() は動かない）
mb  = importlib.import_module("mybot")
bot = getattr(mb, "bot", None)
if bot is None:
    raise SystemExit("mybot.bot が見つかりません")

# 1) discord.py のオリジナル _run_event に戻す（mybot のモンキーパッチを解除）
discord_client = importlib.import_module("discord.client")
bot._run_event = types.MethodType(discord_client.Client._run_event, bot)

# 2) 既存 on_ready リスナーを全削除（循環を断つ）
try:
    if hasattr(bot, "_listeners") and "on_ready" in bot._listeners:
        bot._listeners["on_ready"] = []
        log.info("[safe] cleared existing on_ready listeners")
except Exception as e:
    log.warning(f"[safe] clear listeners failed: {e}")

# 観測イベント
@bot.event
async def on_connect():
    print("[safe] on_connect")

@bot.event
async def on_resumed():
    print("[safe] on_resumed")

@bot.event
async def on_disconnect():
    print("[safe] on_disconnect")

@bot.event
async def on_error(event, *args, **kwargs):
    log.exception(f"[safe] on_error in {event}: {args} {kwargs}")

@bot.event
async def on_ready():
    print(f"[safe] READY as {bot.user} in {len(bot.guilds)} guilds")
    # 初期化（存在すれば）
    try:
        mb.init_database(); log.info("[safe] init_database OK")
    except Exception as e:
        log.warning(f"[safe] init_database skipped: {e}")
    try:
        for g in bot.guilds:
            try: await mb.setup_roles(g)
            except Exception as e:
                log.warning(f"[safe] setup_roles skipped in {getattr(g,'name','?')}: {e}")
    except Exception: pass
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="タスク受付中")
        )
    except Exception: pass

# 20秒待っても READY が来なければ状況を出すウォッチャ
async def _watchdog():
    for i in range(1, 5):
        await asyncio.sleep(5)
        print(f"[safe] waiting READY... {i*5}s")
    print("[safe] still no READY → ゲートウェイ接続はOKか・例外は出ていないか run.log を確認してください")

async def main():
    t = os.getenv("DISCORD_BOT_TOKEN")
    if not t: raise SystemExit("DISCORD_BOT_TOKEN 未設定です")
    if t.count(".") != 2:
        log.warning("[safe] token dots != 2（トークン形式を再確認）")
    asyncio.create_task(_watchdog())  # ← READY待ちウォッチ
    await bot.start(t)

asyncio.run(main())
