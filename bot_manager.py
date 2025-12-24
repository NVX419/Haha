import discord
from discord.ext import commands
from discord import ui
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid

logger = logging.getLogger(__name__)

class TicketButton(ui.Button):
    def __init__(self, label: str, emoji: Optional[str] = None, custom_id: str = None):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            emoji=emoji,
            custom_id=custom_id or f"ticket_{label}"
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = self.view
        if hasattr(view, 'handle_ticket_creation'):
            await view.handle_ticket_creation(interaction, self.label)

class TicketSelectMenu(ui.Select):
    def __init__(self, options: list):
        select_options = []
        for opt in options:
            select_options.append(
                discord.SelectOption(
                    label=opt.get("label", "خيار"),
                    value=opt.get("value", "option"),
                    description=opt.get("description"),
                    emoji=opt.get("emoji")
                )
            )
        
        super().__init__(
            placeholder="اختر نوع التذكرة...",
            options=select_options,
            custom_id="ticket_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = self.view
        if hasattr(view, 'handle_ticket_creation'):
            await view.handle_ticket_creation(interaction, self.values[0])

class TicketView(ui.View):
    def __init__(self, bot, db, settings: Dict[str, Any]):
        super().__init__(timeout=None)
        self.bot = bot
        self.db = db
        self.settings = settings
        
        if settings.get("use_select_menu") and settings.get("menu_options"):
            self.add_item(TicketSelectMenu(settings["menu_options"]))
        else:
            button_count = min(settings.get("button_count", 1), 5)
            labels = settings.get("button_labels", [])
            emojis = settings.get("emoji_ids", [])
            
            for i in range(button_count):
                label = labels[i] if i < len(labels) else f"تذكرة {i+1}"
                emoji = emojis[i] if i < len(emojis) else None
                self.add_item(TicketButton(label, emoji, f"ticket_btn_{i}"))
    
    async def handle_ticket_creation(self, interaction: discord.Interaction, ticket_type: str):
        try:
            user = interaction.user
            guild = interaction.guild
            
            max_tickets = self.settings.get("max_tickets_per_user", 1)
            user_tickets = await self.db.active_tickets.count_documents({"user_id": str(user.id), "is_open": True})
            
            if user_tickets >= max_tickets:
                await interaction.followup.send(
                    f"لديك بالفعل {user_tickets} تذكرة مفتوحة. الحد الأقصى: {max_tickets}",
                    ephemeral=True
                )
                return
            
            category_id = self.settings.get("ticket_category_id")
            category = None
            if category_id:
                category = discord.utils.get(guild.categories, id=int(category_id))
            
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{user.name}",
                category=category,
                topic=f"تذكرة لـ {user.name} - نوع: {ticket_type}"
            )
            
            await ticket_channel.set_permissions(guild.default_role, read_messages=False)
            await ticket_channel.set_permissions(user, read_messages=True, send_messages=True)
            
            support_role_id = self.settings.get("support_role_id")
            mention_text = ""
            if support_role_id:
                support_role = guild.get_role(int(support_role_id))
                if support_role:
                    await ticket_channel.set_permissions(support_role, read_messages=True, send_messages=True)
                    mention_text = f" {support_role.mention}"
            
            welcome_msg = self.settings.get("welcome_message", "شكراً لفتح التذكرة!")
            
            close_button = ui.Button(label="إغلاق التذكرة", style=discord.ButtonStyle.danger, custom_id=f"close_{ticket_channel.id}")
            
            async def close_callback(close_interaction: discord.Interaction):
                await self.close_ticket(close_interaction, ticket_channel)
            
            close_button.callback = close_callback
            close_view = ui.View(timeout=None)
            close_view.add_item(close_button)
            
            await ticket_channel.send(
                f"{user.mention}{mention_text}\n\n{welcome_msg}",
                view=close_view
            )
            
            ticket_doc = {
                "id": str(uuid.uuid4()),
                "ticket_id": str(ticket_channel.id),
                "user_id": str(user.id),
                "username": str(user),
                "ticket_type": ticket_type,
                "is_open": True,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await self.db.active_tickets.insert_one(ticket_doc)
            
            log_doc = {
                "id": str(uuid.uuid4()),
                "ticket_id": str(ticket_channel.id),
                "user_id": str(user.id),
                "username": str(user),
                "action": "opened",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": f"نوع التذكرة: {ticket_type}"
            }
            await self.db.ticket_logs.insert_one(log_doc)
            
            log_channel_id = self.settings.get("log_channel_id")
            if log_channel_id:
                log_channel = guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="🎫 تذكرة جديدة",
                        description=f"**المستخدم:** {user.mention}\n**النوع:** {ticket_type}\n**القناة:** {ticket_channel.mention}",
                        color=discord.Color.green(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    await log_channel.send(embed=embed)
            
            await interaction.followup.send(
                f"✅ تم إنشاء تذكرتك: {ticket_channel.mention}",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error creating ticket: {str(e)}")
            await interaction.followup.send(
                "حدث خطأ أثناء إنشاء التذكرة. يرجى المحاولة مرة أخرى.",
                ephemeral=True
            )
    
    async def close_ticket(self, interaction: discord.Interaction, channel: discord.TextChannel):
        try:
            await interaction.response.defer()
            
            await self.db.active_tickets.update_one(
                {"ticket_id": str(channel.id)},
                {"$set": {"is_open": False, "closed_at": datetime.now(timezone.utc).isoformat()}}
            )
            
            ticket = await self.db.active_tickets.find_one({"ticket_id": str(channel.id)}, {"_id": 0})
            
            log_doc = {
                "id": str(uuid.uuid4()),
                "ticket_id": str(channel.id),
                "user_id": str(interaction.user.id),
                "username": str(interaction.user),
                "action": "closed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": f"تم إغلاق التذكرة بواسطة {interaction.user}"
            }
            await self.db.ticket_logs.insert_one(log_doc)
            
            log_channel_id = self.settings.get("log_channel_id")
            if log_channel_id:
                log_channel = interaction.guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="🔒 تذكرة مغلقة",
                        description=f"**القناة:** {channel.mention}\n**أغلقها:** {interaction.user.mention}\n**المستخدم الأصلي:** <@{ticket['user_id']}>",
                        color=discord.Color.red(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    await log_channel.send(embed=embed)
            
            await channel.send("⏳ سيتم حذف هذه القناة خلال 5 ثوانٍ...")
            await asyncio.sleep(5)
            await channel.delete()
            
        except Exception as e:
            logger.error(f"Error closing ticket: {str(e)}")

class BotManager:
    def __init__(self, db):
        self.bot: Optional[commands.Bot] = None
        self.db = db
        self.bot_task: Optional[asyncio.Task] = None
        self.ticket_message_id: Optional[int] = None
        
    def is_running(self) -> bool:
        return self.bot is not None and not self.bot.is_closed()
    
    async def start_bot(self, token: str):
        if self.is_running():
            await self.stop_bot()
        
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        
        @self.bot.event
        async def on_ready():
            logger.info(f'البوت متصل: {self.bot.user}')
            await self.db.bot_config.update_one({}, {"$set": {"is_running": True}})
            
            settings = await self.db.ticket_settings.find_one({}, {"_id": 0})
            if settings:
                await self.update_ticket_panel(settings)
        
        try:
            self.bot_task = asyncio.create_task(self.bot.start(token))
        except Exception as e:
            logger.error(f"Error starting bot: {str(e)}")
            raise
    
    async def stop_bot(self):
        if self.bot and not self.bot.is_closed():
            await self.bot.close()
        if self.bot_task:
            self.bot_task.cancel()
            try:
                await self.bot_task
            except asyncio.CancelledError:
                pass
        self.bot = None
        self.bot_task = None
    
    async def update_ticket_panel(self, settings: Dict[str, Any]):
        if not self.is_running():
            return
        
        try:
            channel_id = int(settings.get("channel_id"))
            channel = self.bot.get_channel(channel_id)
            
            if not channel:
                logger.error(f"Channel {channel_id} not found")
                return
            
            embed = discord.Embed(
                title="🎫 نظام التذاكر",
                description=settings.get("message", "اضغط على الزر لفتح تذكرة"),
                color=discord.Color.blue()
            )
            
            image_url = settings.get("image_url")
            if image_url:
                if not image_url.startswith("http"):
                    backend_url = "http://localhost:8001"
                    image_url = f"{backend_url}{image_url}"
                embed.set_image(url=image_url)
            
            view = TicketView(self.bot, self.db, settings)
            
            if self.ticket_message_id:
                try:
                    old_message = await channel.fetch_message(self.ticket_message_id)
                    await old_message.delete()
                except:
                    pass
            
            message = await channel.send(embed=embed, view=view)
            self.ticket_message_id = message.id
            
            await self.db.ticket_settings.update_one(
                {"id": settings["id"]},
                {"$set": {"message_id": str(message.id)}}
            )
            
            logger.info(f"Ticket panel updated in channel {channel_id}")
            
        except Exception as e:
            logger.error(f"Error updating ticket panel: {str(e)}")