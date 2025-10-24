import os
import discord
from discord.ext import commands

# 環境変数からトークンを取得
token = os.getenv("DISCORD_BOT_TOKEN")
if not token:
    print("❌ DISCORD_BOT_TOKEN が設定されていません")
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
