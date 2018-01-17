#!/usr/bin/env python3
# Tattle - a Discord bot for transparency in Reddit moderation
# Copyright 2017, 2018 Declan Hoare
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

version = 2
ident = f"Tattle v{version}"
copy = """Tattle - a Discord bot for transparency in Reddit moderation
Copyright 2017, 2018 Declan Hoare

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>."""

print(ident)
print()
print(copy)
print()
print("Loading modules...")
import json, sys, os, pickle, datetime, asyncio, traceback
import praw, discord, discord.gateway

try:
	with open("config.json") as fobj:
		config = json.load(fobj)
except FileNotFoundError:
	print("config.json not found.")
	sys.exit(1)

# Instantiate this to get a namespace that you can easily serialise in
# one go.  It's like a dictionary, but with dot notation.
class thing:
	pass

# All the server sees of a normal exception is "Internal error", because
# exceptions from other people's code could have absolutely anything in
# the messages.  However, exceptions are still useful to kill a command
# when it fails, so safe_exception()s are assumed to have safe messages
# and will be forwarded to the end user.
class safe_exception(Exception):
	pass

try:
	with open("state.pickle", "rb") as fobj:
		state = pickle.load(fobj)
except FileNotFoundError:
	print("state.pickle not found.  Setting up a new database.")
	state = thing()
	# Ok, this is a bit of an odd one.  Reddit gives me my local time in
	# the "created_utc" field, which is actually way ahead of UTC.
	# So I use my local time on first run to initialise the last update
	# field.  Hopefully the same thing happens everywhere.
	state.lastupdate = datetime.datetime.now().timestamp()
	print(f"Cutoff timestamp: {state.lastupdate}")
	state.users = {}
	state.cases = []

def save_state():
	print("Saving.")
	with open("state.pickle", "wb") as fobj:
		pickle.dump(state, fobj)

print("Connecting to Reddit...")
reddit = praw.Reddit(**config["reddit"])
subreddit = reddit.subreddit(config["subreddit"])
print("Connected to Reddit.")

client = discord.Client()

async def send_success(channel, message):
	await client.send_message(channel, embed = discord.Embed(colour = discord.Colour.green()).add_field(name = "Success", value = message).set_footer(text = ident))	

async def send_error(channel, message):
	await client.send_message(channel, embed = discord.Embed(colour = discord.Colour.red()).add_field(name = "Error", value = message).set_footer(text = ident))

commands = {}

# This function takes help page information about your function and
# returns a decorator that inserts the function into the command dict.
# If you leave the default arguments, your function will be excluded
# from the help screen.
def cmd(desc = None, usage = ""):
	def applied(fun):
		commands[fun.__name__] = fun
		fun.desc = desc
		fun.usage = usage
		return fun
	return applied

@cmd("Shows this list of commands.")
async def help(message):
	resp = discord.Embed(title = "Help", colour = discord.Colour.dark_gold()).set_footer(text = ident)
	for n, c in sorted(commands.items()):
		if c.desc:
			resp.add_field(name = f"t:{n} {c.usage}".strip(), value = c.desc, inline = False)
	await client.send_message(message.channel, embed = resp)

@cmd("Shows copyright information.")
async def about(message):
	await client.send_message(message.channel, embed = discord.Embed
		(colour = discord.Colour.orange())
		.add_field
		(
			name = ident,
			value = copy,
			inline = False
		)
		.add_field
		(
			name = "Source code",
			value = "https://github.com/RogueAI42/tattle",
			inline = False
		)
		.set_footer(text = ident))

# Converts a string from the user to a case number if valid, or else
# raises a safe_exception to report the issue.
def validate(casenum):
	try:
		casenum = int(casenum)
	except ValueError:
		raise safe_exception(f"'{casenum}' is not a valid case number.")
	if casenum < 0 or casenum >= len(state.cases) or not state.cases[casenum]:
		raise safe_exception(f"Case #{casenum} does not exist.")
	return casenum

# Shorthand to get the log message for a case.  It might not be obvious,
# but this function is effectively async.
def get_msg(casenum):
	return client.get_message(removed_posts, state.cases[casenum].msgid)

# Shorthand to send a case's embed.
async def do_show(channel, casenum):
	await client.send_message(channel, embed = state.cases[casenum].embed)

# Parser for a command that takes only one argument, which is a case
# number - both t:show and t:strike use this parser.
def parse_num(message):
	try:
		casenum = message.content.split()[1].strip()
	except IndexError:
		raise safe_exception("Please specify a case number.")
	return validate(casenum)

# Parser for a command that takes only one argument, which is a Reddit
# username - both t:info and t:clear use this parser.
def parse_user(message):
	try:
		user = message.content.split()[1].strip().strip("/")
	except IndexError:
		raise safe_exception("Please specify a username.")
	if user.startswith("u/"):
		user = user[2:]
	if user not in state.users:
		raise safe_exception(f"There are no cases associated with /u/{user}.")
	return user

@cmd("Sends an untracked copy of a case's info.", "#")
async def show(message):
	casenum = parse_num(message)
	await do_show(message.channel, casenum)

@cmd("Shows all of a user's cases.", "USER")
async def info(message):
	user = parse_user(message)
	print(state.users[user])
	for casenum in state.users[user]:
		await do_show(message.channel, casenum)

async def do_strike(casenum):
	try:
		msg = await client.delete_message(await get_msg(casenum))
	except discord.NotFound:
		pass
	user = state.cases[casenum].embed.fields[1].value
	if user in state.users and casenum in state.users[user]:
		state.users[user].remove(casenum)
		if not state.users[user]: # Don't keep empty removal lists.
			del state.users[user]
	state.cases[casenum] = None

@cmd("Strikes the given case.  A stricken case will have its log removed, and will not count against the OP.  This is Arnie's favourite command.", "#")
async def strike(message):
	casenum = parse_num(message)
	await do_strike(casenum)
	save_state()
	await send_success(message.channel, f"Case #{casenum} was stricken.")

@cmd("Strikes every case associated with the given user.  See t:strike.", "USER")
async def clear(message):
	user = parse_user(message)
	while user in state.users and state.users[user]:
		await do_strike(state.users[user][0])
	save_state()
	await send_success(message.channel, f"All cases associated with /u/{user} were stricken.")

@cmd("Sets the reason field of a case.", "# REASON...")
async def justify(message):
	try:
		(casenum, reason) = message.content.split(maxsplit = 2)[1:]
	except ValueError:
		raise safe_exception("Please specify a case number and a reason.")
	casenum = validate(casenum)
	state.cases[casenum].embed.set_field_at(5, name = "Reason", 
		value = reason, inline = False)
	try:
		await client.edit_message(await get_msg(casenum), embed = state.cases[casenum].embed)
	except discord.NotFound: # Tracked log removed
		pass
	save_state()
	await send_success(message.channel, f"The reason for case #{casenum} has been set to: {reason}")

@cmd("Lists all tracked users and their removal counts.")
async def list(message):
	l = "\n".join(f"/u/{k} - {len(v)}" for k, v in sorted(state.users.items()))
	if not l:
		l = "\u200b" # Empty embed fields not allowed
	await client.send_message(message.channel,
		embed = discord.Embed(colour = discord.Colour.dark_red())
		.add_field(name = "Removals", value = l)
		.set_footer(text = ident))

# Our loop polls Reddit every 30 seconds, because such a big and
# important and oh so cool Web site wouldn't be caught dead pushing
# events to a puny bot, no siree bob.
async def loop():
	await client.wait_until_ready()
	global removed_posts, sub_mod_talk
	removed_posts = client.get_channel(config["removed_posts"])
	sub_mod_talk = client.get_channel(config["sub_mod_talk"])
	while not client.is_closed:
		try:
			nowtime = datetime.datetime.now().strftime(
				"%H:%M:%S %A %d %B %Y")
			print(f"[{nowtime}] Checking Reddit...")
			newlastupdate = state.lastupdate
			logs = []
			users = []
			casenum = len(state.cases)
			for log in subreddit.mod.log(action = "removelink", limit = None):
				if log.created_utc <= state.lastupdate:
					break
				case = thing()
				case.embed = (discord.Embed
					(colour = discord.Colour.blue())
					.add_field(
						name = "Post title",
						value = log.target_title,
						inline = False)
					.add_field(
						name = "Post author",
						value = log.target_author,
						inline = False)
					.add_field(
						name = "Post link",
						value = "https://reddit.com"
							+ log.target_permalink,
						inline = False)
					.add_field(
						name = "Moderator",
						value = log.mod,
						inline = False)
					.add_field(
						name = "Removal time",
						value = datetime.datetime.fromtimestamp
							(log.created_utc)
							.strftime("%H:%M:%S %A %d %B %Y"),
						inline = False)
					.add_field(
						name = "Reason",
						value = "N/A",
						inline = False)
					.add_field(
						name = "Case #",
						value = casenum,
						inline = False)
					.set_footer(text = ident))
				logs.append(case)
				print(log.target_permalink)
				if log.target_author in state.users:
					state.users[log.target_author].append(casenum)
				else:
					state.users[log.target_author] = [casenum]
				casenum += 1
				
				# Warn mods about repeat offenders
				if len(state.users[log.target_author]) >= 3 and log.target_author not in users:
					users.append(log.target_author)
				newlastupdate = max(newlastupdate, log.created_utc)
			print("Posting...")
			
			# Make room so that the cases can be filled in backwards
			state.cases += [None] * (casenum - len(state.cases))
			
			for log in reversed(logs):
				log.msgid = (await client.send_message(removed_posts, embed = log.embed)).id
				state.cases[int(log.embed.fields[6].value)] = log
			
			for user in users:
				await client.send_message(sub_mod_talk, f"<@&{config['alert_role']}> /u/{user} has made {len(state.users[user])} removed posts.")
			
			print("All log entries have been dispatched to Discord.")
			if newlastupdate > state.lastupdate:
				state.lastupdate = newlastupdate
				save_state()
			
			# Play nice with Reddit.
			await asyncio.sleep(30)
		except KeyboardInterrupt:
			print("Exiting.")
			break
		except:
			traceback.print_exc()
			print("Continuing.")

@client.event
async def on_ready():
	await client.change_presence(game = discord.Game(name = "I Spy"))

@client.event
async def on_message(message):
	if (message.author is message.author.server.owner or message.author.server_permissions.administrator) and message.content.startswith("t:"):
		print(f"{message.author}: {message.content}")
		try:
			command = message.content[2:].split()[0]
		except IndexError:
			await send_error(message.channel, "Please specify a command.")
			return
		if command in commands:
			try:
				await commands[command](message)
			except safe_exception as ex:
				await send_error(message.channel, ex)
			except Exception as ex:
				traceback.print_exc()
				await send_error(message.channel, "Internal error.")
		else:
			await send_error(message.channel, f"Unknown command {command}.")

client.loop.create_task(loop())
print("Connecting to Discord...")
client.run(config["discord"])

