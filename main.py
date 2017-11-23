import pydle
import sys
import datetime
import time
import threading
import re
from config import *

conf = Config('bot.conf')

botnick = conf.get_string('nick', require=True)
botowner = conf.get_string('botowner', require=True)

sasl_uname = conf.get_string('sasl_username')
sasl_pass = conf.get_string('sasl_password', require=(sasl_uname != None))
server = conf.get_string('server', require=True)
port = conf.get_int('port')
tls = conf.get_bool('tls')

OPMODE = conf.get_enum('opmode', ['oper', 'services'])
if not OPMODE:
	OPMODE = "services"

OPERLOGIN = conf.get_string('operuname', require=(OPMODE == 'oper'))
OPERPASS = conf.get_string('operpass', require=(OPMODE == 'oper'))

# Modes
UMODE_CALLERID = conf.get_string('umode_cid')

# InspIRCd-like modes
UMODE_DEAF = conf.get_string('umode_deaf')
MUTEPREFIX = conf.get_string('muteprefix')
UMODE_BLOCKREDIR = conf.get_string('umode_blockredir')

if UMODE_DEAF == None:
	UMODE_DEAF = 'd'
if MUTEPREFIX == None:
	MUTEPREFIX = 'b '
if UMODE_BLOCKREDIR == None:
	UMODE_BLOCKREDIR = ''

# Charybdis (or ircd-seven)-like modes
# UMODE_DEAF = 'D'
# MUTEPREFIX = 'q '
# UMODE_BLOCKREDIR = ''

def ts_to_hr(ts):
	return datetime.datetime.fromtimestamp(ts).strftime("%a, %b %d %Y at %I:%M:%S %p")

# This class written by #python @ freenode
class Duration:

	# Here are the time multipliers
	time_mult = {
		'Y': 31557600,
		'y': 31557600,
		'W': 604800,
		'w': 604800,
		'D': 86400,
		'd': 86400,
		'H': 3600,
		'h': 3600,
		'M': 60,
		'm': 60,
		'S': 1,
		's': 1
	}

	def __init__(self, string):
		self.dur_string = string
		self.parts = self._get_dur_parts()

	def _get_dur_parts(self):
		set_units = []
		for c in self.dur_string:
			if c.lower() in set_units:
				raise KeyError()
			if not c.isnumeric():
				set_units.append(c.lower())

		return {k: int(v) for v, k in re.findall("(\d+)([^\d]+)", self.dur_string) if k != ''}

	def to_seconds(self):
		seconds = 0

		for unit, dur in self.parts.items():
			seconds += self.time_mult[unit] * dur
		return seconds

class BanThread(threading.Thread):
	def __init__(self, client):
		threading.Thread.__init__(self)
		self.client = client

	def run(self):
		while self.client.running:
			for channel in self.client.cm.channels:
				for ban in channel.bans:
					if ban.is_expired():
						channel.del_ban(ban.mask_or_nick, silent=True)
						self.client.notice(channel.name, "Removed expired ban on {}".format(ban.mask_or_nick))
			#print("[BanThread] Thread run")
			time.sleep(15)

class Ban:
	""" This class is the container for a single ban (or temp ban) """

	def __init__(self, client, channel, mask_or_nick, mute=False, duration=-1):
		self.mask_or_nick = mask_or_nick.lower()
		self.client = client
		self.mute = mute
		self.duration = duration
		self.set_on = int(datetime.datetime.now().timestamp())
		self.is_mask = self.is_hostmask()
		self.channel = channel
		self.banned_masks = []

	def is_hostmask(self):
		if self.mask_or_nick.find('!') == -1 and self.mask_or_nick.find('@') == -1:
			return False
		else:
			return True

	def is_expired(self):
		if self.duration == -1:
			return False

		if int(datetime.datetime.now().timestamp()) >= (self.set_on + self.duration):
			return True
		else:
			return False

	# This needs to be in the coroutine for the whois() function to work. Don't ask me why...
	@pydle.coroutine
	def set(self):
		if self.is_mask:
			if self.mute:
				to_set = MUTEPREFIX + self.mask_or_nick
				self.client.rawmsg("MODE", self.channel.name, '+' + to_set.split(' ')[0], to_set.split(' ')[1])
				self.client.notice(self.channel.name, "Mask {} has been muted.".format(self.mask_or_nick))
			else:
				self.client.rawmsg("MODE", self.channel.name, '+b', self.mask_or_nick)
				self.client.notice(self.channel.name, "Mask {} has been banned.".format(self.mask_or_nick))
			if not info['hostname'] in self.banned_masks:
				self.banned_masks.append(info['hostname'])
		else:
			info = yield self.client.whois(self.mask_or_nick.lower())
			if not info:
				return

			if self.mute:
				self.client.rawmsg("MODE", self.channel.name, '+' + (MUTEPREFIX + info['hostname']).split(' ')[0], (MUTEPREFIX + '*!*@' + info['hostname']).split(' ')[1])
				if not info['hostname'] in self.banned_masks:
					self.client.notice(self.channel.name, "Mask *!*@{} has been muted to match nick {}".format(info['hostname'], self.mask_or_nick))
			else:
				self.client.rawmsg("MODE", self.channel.name, '+b', "*!*@" + info['hostname'])
				if self.duration == -1:
					self.client.kick_user(self.mask_or_nick, self.channel.name, reason="You are banned from this channel")
				else:
					self.client.kick_user(self.mask_or_nick, self.channel.name, reason="You are banned from this channel [Expires: {}]".format(ts_to_hr(self.set_on + self.duration)))
				self.client.notice(self.channel.name, "Mask *!*@{} has been banned to match nick {}".format(info['hostname'], self.mask_or_nick))
			if not info['hostname'] in self.banned_masks:
				self.banned_masks.append(info['hostname'])

	def unset(self, silent=False):
		for b in self.banned_masks:
			if self.mute:
				self.client.rawmsg("MODE", self.channel.name, '-' + (MUTEPREFIX + b).split(' ')[0], (MUTEPREFIX + '*!*@' + b).split(' ')[1])
			else:
				self.client.rawmsg("MODE", self.channel.name, '-b', '*!*@' + b)
		if not silent:
			self.client.notice(self.channel.name, "Unset ban on {}.".format(self.mask_or_nick))

class Channel:
	""" This class is the container for a single channel """

	def __init__(self, name, owner):
		self.name = name
		self.owner = owner
		self.admins = []
		self.bans = []

	def add_admin(self, account):
		self.admins.append(account)
		return len(self.admins) - 1

	def del_admin(self, number):
		if number < len(self.admins) and number >= 0:
			self.admins.pop(number)
			return 0
		else:
			return -1

	def change_owner(self, account):
		self.owner = account

	def add_ban(self, client, mask_or_nick, mute=False, duration=-1):
		for ban in self.bans:
			if ban.mask_or_nick.lower() == mask_or_nick.lower():
				return 1
		b = Ban(client, self, mask_or_nick, mute, duration)
		self.bans.append(b)
		b.set()
		return 0

	def del_ban(self, ban, silent=False):
		if str(type(ban)) == "<class 'int'>":
			if ban >= len(self.bans) or ban < 0:
				return 1
			else:
				self.bans[ban].unset(silent)
				self.bans.pop(ban)
				return 0
		else:
			i = 0
			for b in self.bans:
				if b.mask_or_nick.lower() == ban.lower():
					b.unset(silent)
					self.bans.pop(i)
					return 0
				i += 1
			return 1

class ChannelManager:
	""" This class manages all the channels with the bot """

	def __init__(self, chanfilename, adminfilename, banfilename):
		self.chanfilename = chanfilename
		self.adminfilename = adminfilename
		self.banfilename = banfilename
		self.channels = []

	def read_channels(self, client):
		# Read the channels
		file = open(self.chanfilename, 'r')
		list = file.read()
		file.close()

		entries = list.split('\n')
		for entry in entries:
			if entry != "":
				split = entry.split(' ')
				if len(split) != 2:
					print("[ChannelManager] Malformed channel file, aborting")
					return 1
				self.channels.append(Channel(split[0], split[1]))

		# Read the admins
		file = open(self.adminfilename, 'r')
		list = file.read()
		file.close()

		entries = list.split('\n')
		for entry in entries:
			if entry != "":
				split = entry.split(' ')
				if len(split) < 1:
					print("[ChannelManager] Malformed admin file, aborting")
					return 2
				for c in self.channels:
					if c.name == split[0]:
						for admin in split:
							if admin != split[0]:
								c.admins.append(admin)

		# Now read the bans
		file = open(self.banfilename, 'r')
		list = file.read()
		file.close()

		# A line is formed like this:
		# mask_or_nick(string) mute(bool) duration(int) set_on(int) channel.name(string) mask1 mask2 mask3(strings)

		entries = list.split('\n')
		for entry in entries:
			if entry != "":
				split = entry.split(' ', maxsplit=5)
				if len(split) < 5:
					print("[ChannelManager] Malformed bans file, aborting")
					return 3
				for c in self.channels:
					if c.name == split[4]:
						b = Ban(client, c, split[0], (split[1] in ['True']), int(split[2]))
						b.set_on = int(split[3])
						if len(split) == 6:
							# Now we know there are masks assigned to this ban
							for mask in split[5].split(' '):
								b.banned_masks.append(mask)
						c.bans.append(b)

		return 0

	def write_channels(self, silent=False):
		file = open(self.chanfilename, 'w')
		for c in self.channels:
			file.write("{} {}\n".format(c.name, c.owner))
		file.close()
		if not silent:
			print("[ChannelManager] Channels written")

		# Now write the admins
		file = open(self.adminfilename, 'w')
		for c in self.channels:
			for admin in c.admins:
				file.write("{} {}\n".format(c.name, admin))
		file.close()
		if not silent:
			print("[ChannelManager] Admins written")

		# And we write the bans
		file = open(self.banfilename, 'w')
		for c in self.channels:
			for ban in c.bans:
				file.write("{} {} {} {} {}".format(ban.mask_or_nick, ban.mute, ban.duration, ban.set_on, c.name))
				for mask in ban.banned_masks:
					file.write(" {}".format(mask))
				file.write("\n")
		file.close()
		if not silent:
			print("[ChannelManager] Bans written")
		return 0

	def join_channels(self, client):
		for c in self.channels:
			client.join_channel(c.name)

	def add_channel(self, channel, owner):
		self.channels.append(Channel(channel, owner))

	def del_channel(self, channel):
		if str(type(channel)) == "<class 'int'>":
			if channel >= len(self.channels) or channel < 0:
				return 1
			else:
				self.channels.pop(channel)
				return 0
		else:
			i = 0
			for c in channels:
				if c.name == channel:
					self.channels.pop(i)
					return 0
				i += 1
			return 1

	def is_in_channel(self, name):
		i = 0
		for c in self.channels:
			if c.name == name:
				return i
			i += 1
		return -1

class BanBot(pydle.Client):
	""" This is a bot that manages channel bans through PMs and fantasy commands (those come later) """

	mynick = botnick
	channels = []

	def quit(self, message=None):
		super().quit(message)
		self.running = False

	def join_channel(self, channel):
		self.join(channel)
		if OPMODE == "oper":
			self.rawmsg("MODE", channel, "+ao", self.mynick, self.mynick)

	def kick_user(self, user, channel, reason=None):
		if OPMODE == "oper":
			self.rawmsg("SAKICK", channel, user, reason)
		else:
			self.kick(channel, user, reason)

	def on_connect(self):
		super().on_connect()

		# Initialize the channel manager
		self.is_initializing = True
		self.cm = ChannelManager("channels.dat", "admins.dat", "bans.dat")
		self.cm.read_channels(self)

		if OPMODE == "oper":
			self.rawmsg("OPER", OPERLOGIN, OPERPASS)
		# Set modes
		self.rawmsg("MODE", self.mynick, '+' + UMODE_DEAF + UMODE_BLOCKREDIR)

		# Join channels
		self.cm.join_channels(self)

		# Now start the ban loop stuff
		self.running = True
		self.banloop_t = BanThread(self)
		self.banloop_t.start()
		self.is_initializing = False

	def is_admin(self, target, account):
		if account == None:
			return False

		if account == botowner:
			return True

		chan = self.cm.is_in_channel(target)
		if account in self.cm.channels[chan].admins or account == self.cm.channels[chan].owner:
			return True
		return False

	@pydle.coroutine
	def on_message(self, target, source, message):
		super().on_message(target, source, message)

		host = yield self.whois(source.lower())
		cmd = message.split(' ')[0].lower()
		argc = len(message.split(' '))

		if cmd == 'die' or cmd == 'quit':
			if argc == 1:
				if host['account'] == botowner:
					self.cm.write_channels()
					self.quit("Bot shutting down")
				else:
					self.notice(source, "You must have bot owner privileges to execute this command")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'lschans':
			if argc == 1:
				if host['account'] == botowner:
					self.notice(source, "I have \2{}\2 channels".format(len(self.cm.channels)))
					i = 0
					for c in self.cm.channels:
						self.notice(source, "{}. Channel \2{}\2 | Owned by \2{}\2 | Has \2{}\2 bans | Has \2{}\2 admins".format(i, c.name, c.owner, len(c.bans), len(c.admins)))
						i += 1
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'rejoin':
			if argc == 2:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan != -1:
					if self.is_admin(params[1], host['account']):
						self.join_channel(params[1])
					else:
						self.notice(source, "Insufficiennt privileges (You must be an admin on that channel)")
				else:
					self.notice(source, "I'm not in that channel")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'join':
			if argc == 3:
				params = message.split(' ')
				if host['account'] == botowner:
					if params[1][0] == '#':
						if self.cm.is_in_channel(params[1]) == -1:
							self.cm.add_channel(params[1], params[2])
							self.join_channel(params[1])
						else:
							self.notice(source, "I'm already in that channel")
					else:
						self.notice(source, "Invalid channel name")
				else:
					self.notice(source, "You must have bot owner privileges to execute this command")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'leave':
			if argc == 2:
				params = message.split(' ')
				chan = -1

				if params[1].isnumeric():
					chan = int(float(params[1]))
				else:
					chan = self.cm.is_in_channel(params[1])

				if chan >= 0 and chan < len(self.cm.channels):
					if host['account'] == self.cm.channels[chan].owner or host['account'] == botowner:
						channame = self.cm.channels[chan].name
						ret = self.cm.del_channel(chan)
						if ret == 0:
							self.part(channame, "Bot Unassigned")
						else:
							self.notice(source, "An error occurred")
					else:
						self.notice(source, "Insufficient privileges (You must be the bot or channel owner)")
				else:
					self.notice(source, "Invalid channel")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'help':
			if argc == 1:
				self.notice(source, "This list will be created soon, probably")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'ban':
			if argc == 3:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2] == self.mynick:
							self.notice(source, "lolno m8")
							return

						ret = self.cm.channels[chan].add_ban(self, params[2])
						if ret == 1:
							self.notice(source, "There is already a ban on {} in {}.".format(params[2], params[1]))
						else:
							self.notice(source, "Set ban on {} in {}".format(params[2], params[1]))
					else:
						self.notice(source, "Insufficent privileges")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'tempban':
			if argc == 4:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2] == self.mynick:
							self.notice(source, "Nice try ;)")
							return

						# So the ban isn't us, now let's validate the duration argument
						try:
							dur = Duration(params[3])
							ban_secs = dur.to_seconds()
						except:
							self.notice(source, "Invalid duration string")
							return
						ret = self.cm.channels[chan].add_ban(self, params[2], mute=False, duration=ban_secs)
						ban = self.cm.channels[chan].bans[len(self.cm.channels[chan].bans)-1]
						if ret == 1:
							self.notice(source, "There is already a ban on {} in {}".format(params[2], params[1]))
						else:
							self.notice(source, "Set ban on {} to expire on {}".format(params[2], ts_to_hr(ban.set_on + ban_secs)))
		elif cmd == 'lsban':
			if argc == 2:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					i = 0
					for ban in self.cm.channels[chan].bans:
						self.notice(source, "{}. Ban on {} | duration: {} | mute: {} | Currently banned masks: {}".format(i, ban.mask_or_nick, ban.duration, ban.mute, len(ban.banned_masks)))
						i += 1
					self.notice(source, "End of banlist for {}.".format(params[1]))
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'unban':
			if argc == 3:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2].isnumeric():
							ban = int(float(params[2]))
						else:
							ban = params[2]
						ret = self.cm.channels[chan].del_ban(ban)
						if ret == 1:
							self.notice(source, "No such ban on {}".format(params[1]))
						else:
							self.notice(source, "Unbanned {} in {}".format(params[2], params[1]))
					else:
						self.notice(source, "Insufficient privileges")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'sync':
			if argc == 2:
				if self.is_admin(target, host['account']):
					params = message.split(' ')
					chan = self.cm.is_in_channel(params[1])
					if chan == -1:
						self.notice(source, "I'm not in that channel")
					else:
						self.notice(params[1], "Syncing channel bans, this may take awhile")
						for ban in self.cm.channels[chan].bans:
							if ban.mute:
								for masks in ban.banned_masks:
									self.rawmsg("MODE", params[1], '+' + (MUTEPREFIX + masks).split(' ')[0], (MUTEPREFIX + '*!*@' + masks).split(' ')[1])
							else:
								for masks in ban.banned_masks:
									self.rawmsg("MODE", params[1], '+b', '*!*@' + masks)
						self.notice(params[1], "Done syncing channel bans!")
				else:
					self.notice(source, "Insufficient privileges")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'mute':
			if argc == 3:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2] == self.mynick:
							self.notice(source, "lolno m8")
							return

						ret = self.cm.channels[chan].add_ban(self, params[2], mute=True, duration=30)
						if ret == 1:
							self.notice(source, "There is already a mute on {} in {}.".format(params[2], params[1]))
						else:
							self.notice(source, "Added mute on {} in {}".format(params[2], params[1]))
					else:
						self.notice(source, "Insufficient privileges")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'tempmute':
			if argc == 4:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2] == self.mynick:
							self.notice(source, "Nice try ;)")
							return

						# So the ban isn't us, now let's validate the duration argument
						try:
							dur = Duration(params[3])
							ban_secs = dur.to_seconds()
						except:
							self.notice(source, "Invalid duration string")
							return
						ret = self.cm.channels[chan].add_ban(self, params[2], mute=True, duration=ban_secs)
						ban = self.cm.channels[chan].bans[len(self.cm.channels[chan].bans)-1]
						if ret == 1:
							self.notice(source, "There is already a mute on {} in {}".format(params[2], params[1]))
						else:
							self.notice(source, "Set mute on {} to expire on {}".format(params[2], ts_to_hr(ban.set_on + ban_secs)))
		elif cmd == 'access':
			if argc == 2:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					self.notice(source, "Owner of {}: {}".format(params[1], self.cm.channels[chan].owner))
					i = 0
					for admin in self.cm.channels[chan].admins:
						self.notice(source, "{}. Admin {}".format(i, admin))
						i += 1
					self.notice(source, "End of channel access list")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'admin':
			if argc == 3:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						# The issuer is an admin, now check if the user is us or already an admin
						if params[1] == self.mynick:
							self.notice(source, "I'm flattered :)")
						else:
							# So it's not us, does the admin already exist?
							for admin in self.cm.channels[chan].admins:
								if admin == params[2]:
									self.notice(source, "There is already an admin {} on that channel".format(admin))
									return

							# Ok, it's a valid admin, so now we add it to the channel
							ret = self.cm.channels[chan].add_admin(params[2])
							self.notice(params[1], "{} gave admin status to {}".format(source, params[2]))
							self.notice(source, "Admin {} added as {}".format(params[2], ret))
					else:
						self.notice(source, "Insufficient privileges")
			else:
				self.notice(source, "Invalid command invocation")
		elif cmd == 'rmadmin':
			if argc == 3:
				params = message.split(' ')
				chan = self.cm.is_in_channel(params[1])
				if chan == -1:
					self.notice(source, "I'm not in that channel")
				else:
					if self.is_admin(params[1], host['account']):
						if params[2].isnumeric():
							if int(param[2]) >= 0 and int(param[2]) < len(self.cm.channels[chan].admins):
								adminname = self.cm.channels[chan].admins[int(params[2])]
								self.cm.channels[chan].del_admin(int(params[2]))
								self.notice(source, "Removed admin {} on {}".format(adminname, params[1]))
								self.notice(params[1], "{} removed admin status from {}".format(source, adminname))
							else:
								self.notice(source, "No such admin on {}".format(params[1]))
						else:
							i = 0
							for admin in self.cm.channels[chan].admins:
								if admin == params[2]:
									# We found a matching admin
									ret = self.cm.channels[chan].del_admin(i)
									if ret == -1:
										self.notice(source, "An error occurred that I don't understand")
									else:
										self.notice(source, "Removed admin {} on {}".format(params[2], params[1]))
										self.notice(params[1], "{} removed admin status from {}".format(source, params[2]))
					else:
						self.notice(source, "Insufficient privileges")
		elif cmd == 'write':
			if argc == 1:
				if host['account'] == botowner:
					self.cm.write_channels()
					self.notice(source, "Channels Written")
				else:
					self.notice(source, "This command requires bot owner privileges to run.")
			else:
				self.notice(source, "Invalid command invocation")
		else:
			self.notice(source, "Unknown command '{}'. Type 'help' for a list of commands.".format(cmd))

	def on_kick(self, channel, target, by, message):
		super().on_kick(channel, target, by, message)

		if target == self.mynick:
			self.join_channel(channel)
			self.notice(channel, "A user kicked me from this channel while it is on my list of channels. To remove this channel, type /msg {} delete {} .".format(self.mynick, channel))

	def on_part(self, channel, user, message=None):
		super().on_part(channel, user, message)

		if user == self.mynick:
			if self.cm.is_in_channel(channel) != -1:
				self.join_channel(channel)
				self.notice(channel, "I was forced to part this channel while it was on my list of channels. To remove this channel, type /msg {} delete {} .".format(self.mynick, channel))

	def on_join(self, channel, user):
		super().on_join(channel, user)
		if user == self.mynick:
			if self.is_initializing:
				return
			for c in self.cm.channels:
				if c.name == channel:
					return
			self.part(channel, "I was forced to join by something, but I don't wanna be here. Bye!")
		else:
			c = self.cm.is_in_channel(channel)
			for ban in self.cm.channels[c].bans:
				if ban.mask_or_nick.lower() == user.lower():
					ban.set()

	def on_nick_change(self, old, new):
		super().on_nick_change(old, new)
		if old == self.mynick:
			print("My nick is now {}".format(new))
			self.mynick = new

	def on_raw(self, data):
		super().on_raw(data)
		data = str(data).strip('\n')

		if data.startswith('PING'):
			return

		print("[RAW] {}".format(data))

# Connect to the IRC server

try:
	client = BanBot(botnick)
	client.sasl_username = sasl_uname
	client.sasl_password = sasl_pass
	client.connect(server, tls=tls, port=port)
	client.handle_forever()
except KeyboardInterrupt:
	print("^C pressed, exiting")
	client.quit("KeyboardInterrupt recieved, exiting")
	print("The client is disconnected, please wait for the threads to join")

	raise SystemExit(0)
except pydle.ServerError as s:
	print("A socket exeption occurred: {} {}".format(type(s), str(s)))
	raise SystemExit(1)
except BaseException as e:
	print("An unknown error occurred: {} {}".format(type(e), str(e)))
	raise SystemExit(1)
