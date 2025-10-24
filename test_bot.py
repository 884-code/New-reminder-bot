import os
import discord
from discord.ext import commands

# 環境変数からトークンを取得（複数の方法で試行）
token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
if not token:
    print("❌ DISCORD_BOT_TOKEN が設定されていません")
    print("設定されている環境変数:")
    for key, value in os.environ.items():
        if "TOKEN" in key or "DISCORD" in key:
            print(f"  {key}: {value[:10]}...")
    exit(1)

# ボットの設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} がオンラインになりました！")

@bot.command()
async def test(ctx):
    await ctx.reply("✅ テスト成功！")

if __name__ == "__main__":
    print("🚀 ボットを起動中...")
    bot.run(token)
