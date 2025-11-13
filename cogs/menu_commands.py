# cogs/menu_commands.py
import discord
from discord.ext import commands

from menu_recommender import MenuRecommender

class MenuCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.recommender = MenuRecommender()

    # 슬래시 명령
    @discord.app_commands.command(name="menu", description="무작위로 메뉴를 추천합니다.")
    async def menu_slash(self, interaction: discord.Interaction):
        self.recommender.reload()
        gid = interaction.guild_id
        uid = interaction.user.id if interaction.user else None
        picked = self.recommender.recommend(guild_id=gid, user_id=uid)
        if not picked:
            await interaction.response.send_message("추천할 메뉴가 없습니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"오늘은 **{picked['name']}** 어떠세요?")

    # prefix 명령
    @commands.command(name="menu")
    async def menu_prefix(self, ctx: commands.Context):
        self.recommender.reload()
        gid = ctx.guild.id if ctx.guild else None
        uid = ctx.author.id if ctx.author else None
        picked = self.recommender.recommend(guild_id=gid, user_id=uid)
        if not picked:
            await ctx.send("추천할 메뉴가 없습니다.")
            return
        await ctx.send(f"오늘은 **{picked['name']}** 어떠세요?")


async def setup(bot: commands.Bot):
    await bot.add_cog(MenuCog(bot))
