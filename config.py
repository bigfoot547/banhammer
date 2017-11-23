class Config:
	def __init__(self, filename):
		self.filename = filename
		self._values = {}

		file = open(self.filename, 'r')
		confs = file.read()
		file.close()

		lines = confs.split('\n')
		for line in lines:
			if not line == "":
				parse = line.split('=')
				if len(parse) != 2:
					print("Invalid config")
					return 1
				else:
					parse[0] = parse[0].strip(' ')
					self._values[parse[0]] = parse[1].lstrip(' ')

	def get_string(self, index, require=False):
		try:
			return self._values[index]
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None

	def get_list(self, index, require=False):
		try:
			line = self._values[index]
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None
		split = line.split(',')
		for i in range(len(split)):
			split[i] = split[i].strip(' ')
		return split

	def get_bool(self, index, require=False):
		try:
			line = self._values[index]
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None
		line = line.lower()
		return line in ['true', 'yes', '1', 'on', 'enabled']

	def get_int(self, index, require=False):
		try:
			line = self._values[index]
			num = int(line)
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None

		return num

	def get_float(self, index, require=False):
		try:
			line = self._values[index]
			num = float(line)
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None

		return num

	def get_enum(self, index, options, require=False):
		try:
			line = self._values[index]
			if not line in options:
				raise BaseException()
		except:
			if require:
				print("[Config] Invalid config file (option {} not found or not valid)".format(index))
				exit(1)
			return None

		return line
