import os
import re
import random
import shutil
import string
import asyncio
import discord
from pathlib import Path
from contextlib import suppress
from typing import Optional, List, Dict

from redbot.core.utils.chat_formatting import pagify
from redbot.core import commands, data_manager, Config


ALPHANUMERIC_CHARACTERS = string.ascii_letters + string.digits


class Trigger:

    def __init__(self, name: str):
        self.name: str = name
        self.pattern: Optional[re.Pattern] = None
        self.responses: List[dict] = []

    def check(self, author, content) -> Optional[dict]:
        if not self.pattern or not self.responses:
            return None
        match = self.pattern.search(content)
        if match:
            return random.choice(self.responses)
        return None

    @classmethod
    def from_dict(cls, d: dict):
        trigger = cls(d["name"])
        trigger.pattern = re.compile(d["pattern"], re.I) if d["pattern"] else None
        trigger.responses = d["responses"]
        return trigger

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pattern": self.pattern.pattern if self.pattern else None,
            "responses": self.responses,
        }


class TriggerConverter(commands.Converter):
    async def convert(self, ctx, argument: str) -> Trigger:
        trigger = ctx.cog.get_trigger(argument)
        if not trigger:
            raise commands.BadArgument("This trigger doesn't exist! :x:")
        return trigger


class Pal(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=157205897611968514, force_registration=True)
        self.config.register_global(
            triggers=[
                {
                    "name": "default",
                    "pattern": re.compile(r'^$'),
                    "responses": [],
                }
            ]
        )
        self.mention_pattern: re.Pattern = None  # type: ignore
        self.triggers: Dict[str, Trigger] = {}
        self._ready = asyncio.Event()
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        trigger_list = await self.config.triggers()
        self.triggers = {t["name"]: Trigger.from_dict(t) for t in trigger_list}
        await self.bot.wait_until_ready()  # wait until 'self.bot.user' is not None
        self.mention_pattern = re.compile(f'<@!?{self.bot.user.id}> *')
        self._ready.set()

    async def save(self):
        trigger_list = [t.to_dict() for t in self.triggers.values()]
        await self.config.triggers.set(trigger_list)

    async def add_reactions(self, message: discord.Message, reactions: List[str]):
        for r in reactions:
            await message.add_reaction(r)

    def get_trigger(self, trigger_name: str) -> Optional[Trigger]:
        return self.triggers.get(trigger_name)

    def get_trigger_folder(self, trigger: Trigger) -> Path:
        path = data_manager.cog_data_path(self)
        path = path / "trigger_files" / trigger.name
        return path

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if author.bot:
            return
        await self._ready.wait()  # ensure initialization
        content = message.content
        mention_match = self.mention_pattern.match(content)
        if not mention_match:
            return
        # guild = message.guild
        channel = message.channel
        content = content[len(mention_match.group(0)):]  # discard the mention at the start

        for t in self.triggers.values():
            response = t.check(author, content)
            if response:
                if "file" in response:
                    path = self.get_trigger_folder(t)
                    path /= response["file"]
                    with path.open('rb') as file:
                        await channel.send(
                            f"{author.mention} {response['text']}",
                            file=discord.File(file, path.name),
                        )
                else:
                    await channel.send(f"{author.mention} {response['text']}")
                break

    # wait for cog initialization to complete
    async def cog_before_invoke(self, ctx):
        await self._ready.wait()

    @commands.group()
    @commands.is_owner()
    async def pal(self, ctx):
        pass

    @pal.command()
    async def create(self, ctx, trigger_name: str):
        """
        Create a trigger with a given name.
        It won't be active until you add a pattern and responses.
        """
        trigger = self.get_trigger(trigger_name)
        if trigger:
            await ctx.send("This trigger already exists! :x:")
            return
        self.triggers[trigger_name] = Trigger(trigger_name)
        await self.save()
        await ctx.send(f"Trigger `{trigger_name}` has been created!")

    @pal.command()
    async def add(self, ctx, trigger: TriggerConverter, *, response: str):
        """
        Adds a single response to a trigger.
        """
        dict_response: Dict[str, str] = {
            "text": response,
        }
        if ctx.message.attachments:
            # take only the first one
            a: discord.Attachment = ctx.message.attachments[0]
            path = self.get_trigger_folder(trigger)
            os.makedirs(path, exist_ok=True)
            filename = "{}_{}".format(
                ''.join(random.choice(ALPHANUMERIC_CHARACTERS) for i in range(8)),
                a.filename,
            )
            path /= filename
            with path.open('wb') as file:
                await a.save(file)
            dict_response["file"] = filename
        trigger.responses.append(dict_response)
        self.triggers[trigger.name] = trigger
        await self.save()
        await ctx.tick()

    @pal.command()
    async def pattern(self, ctx, trigger: TriggerConverter, *, pattern: str):
        """
        Adds a regex pattern to a trigger.
        """
        if trigger.name == "default":
            await ctx.send(
                "You cannot change the pattern for the `default` trigger! :x:\n"
                "If you want to disable it, please remove all of the responses instead."
            )
            return
        try:
            compiled_pattern = re.compile(pattern, re.I)
        except re.error:
            await ctx.send("Invalid pattern! :x:")
            return
        trigger.pattern = compiled_pattern
        self.triggers[trigger.name] = trigger
        await self.save()
        await ctx.tick()

    @pal.command()
    async def remove(self, ctx, trigger: TriggerConverter):
        """
        Removes responses from a trigger.
        """
        pages = []
        for r in trigger.responses:
            if "file" in r:
                pages.append(f"{r['text']}\n\n**File:** {r['file']}")
            else:
                pages.append(r["text"])

        page = 0
        emojis = ['◀', '❌', '🗑', '▶']
        folder_path = self.get_trigger_folder(trigger)
        msg = await ctx.send("Loading...")
        await self.add_reactions(msg, emojis)

        while True:
            await msg.edit(content=pages[page])
            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add", check=lambda r, u: r.emoji in emojis and u.id == ctx.author.id,
                    timeout=30,
                )
            except asyncio.TimeoutError:
                break
            emoji = reaction.emoji
            if emoji == '◀':
                page -= 1
            elif emoji == '▶':
                page += 1
            elif emoji == '❌':
                break
            elif emoji == '🗑':
                del pages[page]
                r = trigger.responses[page]
                if "file" in r:
                    path = folder_path / r["file"]
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                del trigger.responses[page]
                if not pages:
                    await ctx.send("There's no more responses to delete!")
                    break
            page %= len(pages)
            await reaction.remove(user)
        await msg.clear_reactions()

    @pal.command()
    async def delete(self, ctx, trigger: TriggerConverter):
        """
        Deletes a trigger with a given name.
        """
        if trigger.name == "default":
            await ctx.send(
                "You cannot delete the `default` trigger! :x:\n"
                "If you want to disable it, please remove all of the responses instead."
            )
            return
        folder_path = self.get_trigger_folder(trigger)
        with suppress(FileNotFoundError):
            shutil.rmtree(folder_path)
        del self.triggers[trigger.name]
        await self.save()
        await ctx.send(f"Trigger `{trigger.name}` has been deleted!")

    @pal.command()
    async def list(self, ctx):
        """
        Lists all triggers.
        """
        trigger_names = ', '.join(t.name for t in self.triggers.values())
        pages = list(pagify(trigger_names, delims=[' '], shorten_by=10))
        if not pages:
            await ctx.send("There's no triggers to display!")
            return
        for p in pages:
            await ctx.send(f"```\n{p}\n```")

    @pal.command()
    async def info(self, ctx, trigger: TriggerConverter):
        """
        Shows info about a trigger.
        """
        pattern = trigger.pattern.pattern if trigger.pattern else None
        e = discord.Embed()
        e.set_author(name=f"Trigger: {trigger.name}")
        e.description = f"**Pattern:**\n\n`{pattern}`"
        for i, r in enumerate(trigger.responses, start=1):
            text = r["text"]
            if len(text) > 1024:
                text = text[:1020] + "..."
            if "file" in r:
                e.add_field(name=f"Response #{i}", value=f"{text}\n\n**File:** {r['file']}")
            else:
                e.add_field(name=f"Response #{i}", value=text)
        await ctx.send(embed=e)
