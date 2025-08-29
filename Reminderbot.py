# -*- coding: utf-8 -*-
# Reminderbot.py (cleaned up version)
import os, re, json, sqlite3, logging, asyncio
from datetime import datetime, timedelta
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

import time as _time
RENAME_COOLDOWN_SEC = 30.0
_last_rename_at = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")]
)
logger = logging.getLogger("taskbot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!", "！", "/"),
    intents=intents,
    case_insensitive=True,
    help_command=None,
    max_messages=100,
)

DB_PATH = "reminder_bot.db"

def db_exec(q: str, params: tuple = (), fetch=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(q, params)
    rows = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return rows

def init_db():
    db_exec("""CREATE TABLE IF NOT EXISTS admins(user_id INTEGER, guild_id INTEGER, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,guild_id))""")
    db_exec("""CREATE TABLE IF NOT EXISTS instructors(user_id INTEGER, guild_id INTEGER, target_users TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,guild_id))""")
    db_exec("""CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, instructor_id INTEGER, assignee_id INTEGER,
        task_name TEXT, due_date TIMESTAMP, status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        message_id INTEGER, channel_id INTEGER, reminder_sent INTEGER DEFAULT 0, thread_id INTEGER)""")
    
    # thread_idカラムが存在するかチェック
    cols = db_exec("PRAGMA table_info(tasks)", fetch=True)
    if "thread_id" not in [c[1] for c in cols]:
        db_exec("ALTER TABLE tasks ADD COLUMN thread_id INTEGER")

def is_admin(uid:int, gid:int) -> bool:
    return bool(db_exec("SELECT 1 FROM admins WHERE user_id=? AND guild_id=?", (uid,gid), fetch=True))

def is_instructor(uid:int, gid:int) -> bool:
    return bool(db_exec("SELECT 1 FROM instructors WHERE user_id=? AND guild_id=?", (uid,gid), fetch=True))

def insert_task(gid:int, iid:int, aid:int, name:str, due:datetime, mid:int=None, cid:int=None) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tasks (guild_id,instructor_id,assignee_id,task_name,due_date,message_id,channel_id) VALUES (?,?,?,?,?,?,?)",
        (gid, iid, aid, name, due, mid, cid)
    )
    tid = cur.lastrowid or 0
    conn.commit()
    conn.close()
    return tid

def get_task(tid:int):
    r = db_exec("SELECT * FROM tasks WHERE id=?", (tid,), fetch=True)
    return r[0] if r else None

async def ensure_mgmt(guild:discord.Guild) -> Optional[discord.TextChannel]:
    for name in ("task-management","タスク管理"):
        ch = discord.utils.get(guild.channels, name=name)
        if isinstance(ch, discord.TextChannel):
            return ch
    try:
        return await guild.create_text_channel("task-management", 
                                             overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=False)})
    except Exception as e:
        logger.error(f"mgmt create failed: {e}")
        return None

async def ensure_personal(guild:discord.Guild, user:discord.Member) -> Optional[discord.TextChannel]:
    name = f"to-{user.display_name}".lower().replace(" ","-")
    ch = discord.utils.get(guild.channels, name=name)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        ow = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        return await guild.create_text_channel(name=name, overwrites=ow, topic=f"{user.display_name} の個人タスク")
    except Exception as e:
        logger.error(f"personal create failed: {e}")
        return None

def parse_date(s:str) -> Optional[datetime]:
    now = datetime.now()
    t = s.strip().lower()
    m = re.search(r'(\d{1,2}):(\d{2})$', t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        t = t[:m.start()].strip()
    else:
        hour, minute = 23, 59
    
    pats = [
        (r'^(今日|today)$', lambda m: now),
        (r'^(明日|tomorrow)$', lambda m: now + timedelta(days=1)),
        (r'^(明後日|day after tomorrow)$', lambda m: now + timedelta(days=2)),
        (r'^(昨日|yesterday)$', lambda m: now - timedelta(days=1)),
        (r'^(\d+)\s*(日後|days?)$', lambda m: now + timedelta(days=int(m.group(1)))),
        (r'^(\d+)\s*(週間後|weeks?)$', lambda m: now + timedelta(weeks=int(m.group(1)))),
        (r'^(\d+)\s*(時間後|hours?)$', lambda m: now + timedelta(hours=int(m.group(1)))),
        (r'^(\d+)\s*(分後|mins?|minutes?)$', lambda m: now + timedelta(minutes=int(m.group(1))))
    ]
    
    for pat, fn in pats:
        mm = re.match(pat, t)
        if mm:
            dt = fn(mm)
            return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    wk = {
        '月':0,'火':1,'水':2,'木':3,'金':4,'土':5,'日':6,
        'monday':0,'tuesday':1,'wednesday':2,'thursday':3,'friday':4,'saturday':5,'sunday':6,
        'mon':0,'tue':1,'wed':2,'thu':3,'fri':4,'sat':5,'sun':6
    }
    
    for name, num in wk.items():
        if name in t:
            d = num - now.weekday()
            d += 7 if d <= 0 else 0
            return (now + timedelta(days=d)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    abs_p = [
        r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$',
        r'^(\d{1,2})[/-](\d{1,2})$',
        r'^(\d{4})年(\d{1,2})月(\d{1,2})日$',
        r'^(\d{1,2})月(\d{1,2})日$'
    ]
    
    for pat in abs_p:
        mm = re.match(pat, t)
        if mm:
            g = mm.groups()
            if len(g) == 3 and len(g[0]) == 4:
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif len(g) == 3:
                mo, d, y = int(g[0]), int(g[1]), now.year
            else:
                mo, d, y = int(g[0]), int(g[1]), now.year
            try:
                return datetime(y, mo, d, hour, minute)
            except ValueError:
                return None
    return None

STATUS_COLORS = {
    'pending': discord.Color.red(),
    'accepted': discord.Color.gold(),
    'completed': discord.Color.green(),
    'declined': discord.Color.dark_gray(),
    'abandoned': discord.Color.orange()
}

STATUS_EMOJI = {
    'pending': '🟥',
    'accepted': '🟨',
    'completed': '🟩',
    'declined': '⚪',
    'abandoned': '⚠️'
}

STATUS_NAME_JP = {
    'pending': '未受託',
    'accepted': '進行中',
    'completed': '完了',
    'declined': '辞退',
    'abandoned': '問題'
}

# 最小限のメイン用Embed（タスク名と期日のみ・日本語）
def build_main_embed_jp(task_row) -> discord.Embed:
    title = f"📋 {task_row[4]}"
    try:
        due_raw = task_row[5]
        try:
            due_ts = int(datetime.fromisoformat(str(due_raw)).timestamp())
        except Exception:
            due_ts = int(datetime.strptime(str(due_raw), "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        due_ts = int(datetime.now().timestamp())
    emb = discord.Embed(title=title, description=f"期日: <t:{due_ts}:F>", color=discord.Color.gold())
    return emb

# 詳細用Embed（スレッド内・日本語）
def build_detail_embed_jp(task_row, status: Optional[str]=None) -> discord.Embed:
    st = status or task_row[6]
    try:
        due_raw = task_row[5]
        try:
            due_ts = int(datetime.fromisoformat(str(due_raw)).timestamp())
        except Exception:
            due_ts = int(datetime.strptime(str(due_raw), "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        due_ts = int(datetime.now().timestamp())
    emb = discord.Embed(title=f"📋 {task_row[4]}", color=STATUS_COLORS.get(st, discord.Color.blurple()))
    emb.add_field(name="期日", value=f"<t:{due_ts}:F>", inline=True)
    emb.add_field(name="状態", value=f"{STATUS_EMOJI.get(st,'⚪')} {STATUS_NAME_JP.get(st, st)}", inline=True)
    emb.add_field(name="更新", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
    emb.set_footer(text=f"Task ID: {task_row[0]}")
    return emb

class TaskView(discord.ui.View):
    def __init__(self, tid:int, aid:int, iid:int, status:str):
        super().__init__(timeout=None)
        self.tid, self.aid, self.iid, self.status = tid, aid, iid, status
        self._setup()
    
    def _setup(self):
        self.clear_items()
        if self.status == 'pending':
            self.add_item(AcceptButton(self.tid))
            self.add_item(DeclineButton(self.tid))
        elif self.status == 'accepted':
            self.add_item(CompleteButton(self.tid))
            self.add_item(AbandonButton(self.tid))
        elif self.status == 'completed':
            self.add_item(UndoButton(self.tid))
        elif self.status == 'abandoned':
            # 問題状態でもアクション可能にする（受託に戻す／完了）
            self.add_item(UndoButton(self.tid))
            self.add_item(CompleteButton(self.tid))

class _BaseBtn(discord.ui.Button):
    def __init__(self, label, style, cid):
        super().__init__(label=label, style=style, custom_id=cid)
    
    async def _notify_instructor(self, guild: discord.Guild, instructor_id: int, assignee_id: int, tname: str, status: str, thread: Optional[discord.Thread]):
        inst = guild.get_member(instructor_id)
        ass = guild.get_member(assignee_id)
        if not inst:
            return
        msg = f"📣 タスク状態が更新されました\nタスク: {tname}\n担当: {ass.mention if ass else assignee_id}\n状態: {STATUS_EMOJI.get(status,'⚪')} {STATUS_NAME_JP.get(status,status)}"
        try:
            if thread:
                msg += f"\nスレッド: {thread.mention}"
        except Exception:
            pass
        try:
            await inst.send(msg)
        except Exception:
            ch = await ensure_mgmt(guild)
            if ch:
                await ch.send(inst.mention + "\n" + msg)

    async def _rename_thread(self, guild: discord.Guild, task_id: int, status: str):
        # thread_id からスレッド取得
        rows = db_exec("SELECT thread_id, task_name FROM tasks WHERE id=?", (task_id,), fetch=True)
        if not rows or not rows[0] or not rows[0][0]:
            return None
        tid, tname = rows[0][0], rows[0][1] if len(rows[0]) > 1 else None
        th = guild.get_channel(tid)
        if not isinstance(th, discord.Thread):
            try:
                th = await guild.fetch_channel(tid)
            except Exception:
                th = None
        if not isinstance(th, discord.Thread):
            return None
        # クールダウン
        now = _time.monotonic()
        last = _last_rename_at.get(th.id, 0.0)
        if now - last < RENAME_COOLDOWN_SEC:
            return th
        cur = th.name or ""
        em = STATUS_EMOJI.get(status, '⚪')
        for e in ("🟥","🟨","🟩","⚠️","❌","⚪"):
            if cur.startswith(e):
                new = em + cur[len(e):]
                break
        else:
            new = f"{em} {cur}"
        new = new.lstrip()
        if new != cur:
            try:
                await th.edit(name=new)
                _last_rename_at[th.id] = now
            except Exception:
                pass
        return th

    async def _handle(self, it:discord.Interaction, new_status:str):
        t_rows = db_exec("SELECT * FROM tasks WHERE id=?", (int(self.custom_id.split('_')[-1]),), fetch=True)
        if not t_rows:
            await it.response.send_message("❌ タスクが見つかりません。", ephemeral=True)
            return
        t = t_rows[0]
        if len(t) < 7:
            await it.response.send_message("❌ タスクデータが不正です。", ephemeral=True)
            return
        if it.user.id != t[3]:
            await it.response.send_message("❌ あなたは担当者ではありません。", ephemeral=True)
            return
        db_exec("UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, t[0]))
        t2_rows = db_exec("SELECT * FROM tasks WHERE id=?", (t[0],), fetch=True)
        if not t2_rows:
            await it.response.send_message("❌ 再読込に失敗しました。", ephemeral=True)
            return
        t2 = t2_rows[0]
        # メッセージは詳細Embedで更新（日本語）
        try:
            await it.response.edit_message(embed=build_detail_embed_jp(t2, new_status), view=TaskView(t2[0], t2[3], t2[2], new_status))
        except Exception:
            try:
                await it.message.edit(embed=build_detail_embed_jp(t2, new_status), view=TaskView(t2[0], t2[3], t2[2], new_status))
            except Exception:
                pass
        # 先にスレッド名を更新
        thread = await self._rename_thread(it.guild, t2[0], new_status)
        # 指示者に通知
        await self._notify_instructor(it.guild, t2[2], t2[3], t2[4], new_status, thread)

class AcceptButton(_BaseBtn):
    def __init__(self, tid:int):
        super().__init__("✅ 受託", discord.ButtonStyle.success, f"accept_task_{tid}")
    async def callback(self, it):
        await self._handle(it, 'accepted')

class DeclineButton(_BaseBtn):
    def __init__(self, tid:int):
        super().__init__("❌ 辞退", discord.ButtonStyle.danger, f"decline_task_{tid}")
    async def callback(self, it):
        await self._handle(it, 'declined')

class CompleteButton(_BaseBtn):
    def __init__(self, tid:int):
        super().__init__("📝 完了", discord.ButtonStyle.success, f"complete_task_{tid}")
    async def callback(self, it):
        await self._handle(it, 'completed')

class AbandonButton(_BaseBtn):
    def __init__(self, tid:int):
        super().__init__("⚠️ 問題", discord.ButtonStyle.danger, f"abandon_task_{tid}")
    async def callback(self, it):
        await self._handle(it, 'abandoned')

class UndoButton(_BaseBtn):
    def __init__(self, tid:int):
        super().__init__("↩️ 戻す", discord.ButtonStyle.secondary, f"undo_completion_{tid}")
    async def callback(self, it):
        await self._handle(it, 'accepted')

# タスク通知（個人CHに最小、スレッドで詳細、日本語、thread_id保存、指示者に通知）
async def send_task_notification_jp(guild: discord.Guild, assignee: discord.Member, instructor: discord.Member, task_row: tuple):
    # 個人CH確保
    ch = await ensure_personal(guild, assignee)
    if not ch:
        try:
            ch = await assignee.create_dm()
        except Exception:
            ch = None
    # メインは最小表示（ボタンなし）
    main_msg = None
    if ch:
        main_msg = await ch.send(assignee.mention, embed=build_main_embed_jp(task_row))
    # 詳細スレッド（ボタン付き）
    thread = None
    try:
        base = main_msg if isinstance(main_msg, discord.Message) else None
        if base and hasattr(base, 'create_thread'):
            thread = await base.create_thread(name=f"{STATUS_EMOJI.get(task_row[6],'⚪')} {task_row[4]} - 詳細", auto_archive_duration=60, reason="タスク詳細")
        elif ch and hasattr(ch, 'create_thread'):
            thread = await ch.create_thread(name=f"{STATUS_EMOJI.get(task_row[6],'⚪')} {task_row[4]} - 詳細", type=discord.ChannelType.public_thread)
    except Exception:
        thread = None
    # 詳細情報投稿（ここにボタンを付ける）
    if isinstance(thread, discord.Thread):
        try:
            det = build_detail_embed_jp(task_row)
            await thread.send(embed=det, view=TaskView(task_row[0], task_row[3], task_row[2], task_row[6]))
        except Exception:
            pass
        try:
            db_exec("UPDATE tasks SET thread_id=?, message_id=? WHERE id=?", (thread.id, main_msg.id if main_msg else None, task_row[0]))
        except Exception:
            pass
    # 指示者に通知
    try:
        msg = f"📣 タスクを指示しました\nタスク: {task_row[4]}\n担当: {assignee.mention}\n期日: {task_row[5]}"
        if isinstance(thread, discord.Thread):
            msg += f"\nスレッド: {thread.mention}"
        await instructor.send(msg)
    except Exception:
        mg = await ensure_mgmt(guild)
        if mg:
            await mg.send(instructor.mention + "\n" + msg)

@bot.event
async def on_ready():
    logger.info(f"{bot.user} logged in. Guilds={len(bot.guilds)}")
    init_db()
    try:
        logger.info("Text commands: %s", ", ".join(sorted(c.name for c in bot.commands)))
    except Exception:
        pass
    try:
        for g in bot.guilds:
            await bot.tree.sync(guild=g)
        logger.info("Slash commands synced.")
    except Exception as e:
        logger.error(f"Slash sync error: {e}")

@bot.event
async def on_message(message:discord.Message):
    if message.author.bot:
        return
    text = message.content.strip()
    if re.match(rf"^(?:<@!?{bot.user.id}>\s*)?[/!！]?\s*setup\b", text, flags=re.I):
        ctx = await bot.get_context(message)
        await setup_cmd(ctx)
        return
    await bot.process_commands(message)

@bot.command(name="setup", aliases=["init","セットアップ"])
async def setup_cmd(ctx:commands.Context):
    if not ctx.guild:
        await ctx.reply("❌ サーバー内で実行してください")
        return
    db_exec("INSERT OR IGNORE INTO admins(user_id,guild_id) VALUES(?,?)", (ctx.author.id, ctx.guild.id))
    await ctx.reply("✅ Setup complete. Use `!channels` → `!test`")

@bot.command(name="channels")
async def channels_cmd(ctx:commands.Context):
    try:
        if not ctx.guild:
            return
        if not is_admin(ctx.author.id, ctx.guild.id):
            await ctx.reply("❌ Admin only")
            return
        created = []
        mg = await ensure_mgmt(ctx.guild)
        if mg:
            created.append(mg.name)
        cnt = 0
        for m in ctx.guild.members:
            if m.bot:
                continue
            if cnt >= 10:
                break
            ch = await ensure_personal(ctx.guild, m)
            if ch:
                created.append(ch.name)
                cnt += 1
            await asyncio.sleep(0.3)
        await ctx.reply("✅ Channels: " + (", ".join(created) if created else "(none)"))
    except Exception as e:
        logging.error("channels_cmd failed", exc_info=True)
        await ctx.reply(f"❌ channels error\n`{type(e).__name__}: {e}`")

@bot.command(name="test")
async def test_cmd(ctx:commands.Context):
    try:
        if not ctx.guild:
            return
        due = datetime.now() + timedelta(hours=1, minutes=5)
        tid = insert_task(ctx.guild.id, ctx.author.id, ctx.author.id, "テストタスク", due, ctx.message.id, ctx.channel.id)
        task = get_task(tid)
        if not task:
            logger.error(f"DB readback failed for task id={tid}")
            await ctx.reply("❌ タスク作成後の読み出しに失敗しました。もう一度お試しください。")
            return
        await send_task_notification_jp(ctx.guild, ctx.author, ctx.author, task)
        await ctx.reply(f"✅ テスト作成 (ID={tid})")
    except Exception as e:
        logging.error("test_cmd failed", exc_info=True)
        try:
            await ctx.reply(f"❌ test error\n`{type(e).__name__}: {e}`")
        except Exception:
            pass

@bot.command(name="ping")
async def ping_cmd(ctx:commands.Context):
    await ctx.reply("pong")

@bot.command(name="assign", aliases=["指示","assign_task"])
async def assign_cmd(ctx: commands.Context, *, content: str):
    try:
        if not ctx.guild:
            await ctx.reply("❌ サーバー内で実行してください")
            return
        # 対象ユーザー（Bot以外のメンション）
        assignees = [m for m in ctx.message.mentions if m.id != bot.user.id]
        if not assignees:
            await ctx.reply("❌ 指示対象のユーザーをメンションしてください。例: !assign @太郎, 明日 18:00, レポート提出")
            return
        # メンションを本文から取り除き、 "期日, タスク名" を抽出
        text_wo_mentions = re.sub(r'<@!?[0-9]+>', '', content).strip()
        m = re.search(r'[，,]', text_wo_mentions)
        if not m:
            await ctx.reply("❌ 形式: !assign @ユーザー, 期日, タスク名（半角`,` を2つ）")
            return
        rest = text_wo_mentions[m.end():].strip()
        parts = [p.strip() for p in re.split(r'[，,]', rest, maxsplit=1)]
        if len(parts) < 2:
            await ctx.reply("❌ 形式: !assign @ユーザー, 期日, タスク名")
            return
        due_str, task_name = parts[0], parts[1]
        if not task_name:
            await ctx.reply("❌ タスク名が空です。")
            return
        due_dt = parse_date(due_str)
        if not due_dt:
            await ctx.reply("❌ 期日が読めませんでした。例: 明日 18:00 / 3日後 / 金曜 14:30 / 2025/08/23 09:00")
            return
        created = 0
        for member in assignees:
            tid = insert_task(ctx.guild.id, ctx.author.id, member.id, task_name, due_dt, ctx.message.id, ctx.channel.id)
            task_row = get_task(tid)
            if not task_row:
                continue
            await send_task_notification_jp(ctx.guild, member, ctx.author, task_row)
            created += 1
        if created:
            await ctx.reply(f"✅ {created}件のタスクを指示しました。")
        else:
            await ctx.reply("❌ 作成に失敗しました。")
    except Exception as e:
        logging.error("assign_cmd failed", exc_info=True)
        try:
            await ctx.reply(f"❌ assign error\n`{type(e).__name__}: {e}`")
        except Exception:
            pass

# 共通ハンドラ: スラッシュ指示の実装本体
async def _handle_assign_slash(it: discord.Interaction, user: discord.Member, due: str, title: str):
    if not it.guild:
        await it.response.send_message("❌ サーバー内で実行してください", ephemeral=True)
        return
    due_dt = parse_date(due)
    if not due_dt:
        await it.response.send_message("❌ 期日が読めませんでした。例: 明日 18:00 / 3日後 / 金曜 14:30 / 2025/08/23 09:00", ephemeral=True)
        return
    tid = insert_task(it.guild.id, it.user.id, user.id, title, due_dt, getattr(it.message, 'id', None), getattr(it.channel, 'id', None))
    row = get_task(tid)
    if not row:
        await it.response.send_message("❌ 作成に失敗しました。", ephemeral=True)
        return
    await send_task_notification_jp(it.guild, user, it.user, row)
    await it.response.send_message(f"✅ {user.mention} にタスクを指示しました。", ephemeral=True)

# 既存の /assign を共通ハンドラに委譲
@bot.tree.command(name="assign", description="タスクを指示（@ユーザー, 期日, タスク名）")
@app_commands.describe(user="対象ユーザー", due="期日（例: 明日 18:00 / 3日後 / 2025/09/01 09:00）", title="タスク名")
async def assign_slash(it: discord.Interaction, user: discord.Member, due: str, title: str):
    try:
        await _handle_assign_slash(it, user, due, title)
    except Exception as e:
        logging.error("assign_slash failed", exc_info=True)
        try:
            if not it.response.is_done():
                await it.response.send_message(f"❌ assign error\n`{type(e).__name__}: {e}`", ephemeral=True)
            else:
                await it.followup.send(f"❌ assign error\n`{type(e).__name__}: {e}`", ephemeral=True)
        except Exception:
            pass

# 日本語エイリアス /指示 も同じ本体に委譲
@bot.tree.command(name="指示", description="タスクを指示（@ユーザー, 期日, タスク名）")
@app_commands.describe(user="対象ユーザー", due="期日（例: 明日 18:00 / 3日後 / 2025/09/01 09:00）", title="タスク名")
async def 指示(it: discord.Interaction, user: discord.Member, due: str, title: str):
    try:
        await _handle_assign_slash(it, user, due, title)
    except Exception as e:
        logging.error("assign_slash(jp) failed", exc_info=True)
        try:
            if not it.response.is_done():
                await it.response.send_message(f"❌ assign error\n`{type(e).__name__}: {e}`", ephemeral=True)
            else:
                await it.followup.send(f"❌ assign error\n`{type(e).__name__}: {e}`", ephemeral=True)
        except Exception:
            pass

@tasks.loop(minutes=5)
async def check_reminders():
    now = datetime.now()
    soon = now + timedelta(hours=1)
    rows = db_exec("SELECT id,guild_id,assignee_id,task_name,due_date FROM tasks WHERE status='accepted' AND reminder_sent=0 AND due_date>? AND due_date<=?", (now,soon), fetch=True)
    for tid, gid, aid, tname, due in rows:
        guild = bot.get_guild(gid)
        if not guild:
            continue
        user = guild.get_member(aid)
        if not user:
            continue
        try:
            try:
                due_ts = int(datetime.fromisoformat(str(due)).timestamp())
            except Exception:
                due_ts = int(datetime.strptime(str(due), "%Y-%m-%d %H:%M:%S").timestamp())
            emb = discord.Embed(title="⏰ Task Reminder", description=f"**{tname}**\nDue in less than 1 hour!", color=discord.Color.orange())
            emb.add_field(name="Due", value=f"<t:{due_ts}:F>", inline=True)
            try:
                await user.send(embed=emb)
            except Exception:
                ch = await ensure_personal(guild, user)
                if ch:
                    await ch.send(user.mention, embed=emb)
            db_exec("UPDATE tasks SET reminder_sent=1 WHERE id=?", (tid,))
        except Exception as e:
            logging.error(f"reminder failed: {e}")
            db_exec("UPDATE tasks SET reminder_sent=1 WHERE id=?", (tid,))

@check_reminders.before_loop
async def _b1():
    await bot.wait_until_ready()

@tasks.loop(minutes=1)
async def heartbeat_check():
    try:
        if heartbeat_check.current_loop % 5 == 0:
            logger.info(f"Heartbeat OK. Guilds={len(bot.guilds)} Latency={round(bot.latency*1000)}ms")
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")

@heartbeat_check.before_loop
async def _b2():
    await bot.wait_until_ready()

@tasks.loop(hours=1)
async def cleanup_memory():
    try:
        if hasattr(bot,"_connection") and hasattr(bot._connection,"_messages"):
            bot._connection._messages.clear()
        logger.info("Memory cleanup done.")
    except Exception as e:
        logger.error(f"cleanup error: {e}")

@cleanup_memory.before_loop
async def _b3():
    await bot.wait_until_ready()

@bot.event
async def on_command_error(ctx:commands.Context, error:Exception):
    if isinstance(error, commands.CommandNotFound):
        text = ctx.message.content.strip()
        if re.match(r"^[/!！]?\s*setup\b", text, flags=re.I):
            await setup_cmd(ctx)
            return
        return
    logging.error(f"Command error: {error}", exc_info=True)
    try:
        err_type = type(error).__name__
        await ctx.reply(f"❌ An error occurred.\n`{err_type}: {error}`")
    except:
        pass

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    logging.error(f"App command error: {error}", exc_info=True)
    try:
        msg = f"❌ {type(error).__name__}: {error}"
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_error(event_method, *args, **kwargs):
    logging.error(f"on_error in {event_method}", exc_info=True)

@bot.command(name="syncslash", aliases=["sync","fixslash","スラッシュ同期"])
async def syncslash_cmd(ctx: commands.Context):
    try:
        if not ctx.guild:
            await ctx.reply("❌ サーバー内で実行してください")
            return
        if not is_admin(ctx.author.id, ctx.guild.id):
            await ctx.reply("❌ Admin only")
            return
        # ギルド用コマンドを一旦クリアしてから、グローバル定義をコピー→同期
        try:
            bot.tree.clear_commands(guild=ctx.guild)
        except Exception:
            pass
        try:
            bot.tree.copy_global_to(guild=ctx.guild)
        except Exception:
            pass
        # グローバルとギルド両方を同期（片方だけの環境でもOK）
        try:
            await bot.tree.sync()
        except Exception:
            pass
        updated = await bot.tree.sync(guild=ctx.guild)
        await ctx.reply(f"✅ Slash commands synced ({len(updated)} in guild). 再度 /assign や /指示 をお試しください。")
    except Exception as e:
        logging.error("syncslash_cmd failed", exc_info=True)
        try:
            await ctx.reply(f"❌ sync error\n`{type(e).__name__}: {e}`")
        except Exception:
            pass

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN が未設定です。export DISCORD_BOT_TOKEN=... で設定してください。")
    bot.run(token)