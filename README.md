# Discord Reminder Bot

## Local Run
```bash
export DISCORD_BOT_TOKEN="<your token>"
python3 Reminderbot.py
```

## Deploy to Render (Blueprint)
1. Push this repo to GitHub
2. On Render, New → Blueprint → select this repo
3. Set Environment Variable: `DISCORD_BOT_TOKEN`
4. Deploy

Files:
- `render.yaml`: Worker definition (24/7)
- `Dockerfile`: Container build (optional if using Docker)
- `.dockerignore`: Exclude local files from image

## Commands
- `!assign` / `/assign` / `/指示` でタスク割当
- スレッド内のボタンで状態変更（日本語） 