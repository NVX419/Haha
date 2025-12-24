import os
import discord
from discord.ext import commands

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

# تشغيل البوت
bot.run(os.getenv("DISCORD_TOKEN"))
