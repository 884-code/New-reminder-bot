import os, discord, asyncio
intents = discord.Intents.none()
intents.guilds = True  # READYにはこれだけで十分
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"[mini] READY as {client.user} in {len(client.guilds)} guilds")

async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN 未設定です")
    await client.start(token)

asyncio.run(main())
