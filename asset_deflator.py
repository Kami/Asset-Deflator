# -*- coding: utf-8 -*-
#
# Name: Asset Deflator
# Description: Script for minifying / compiling / compressing your website static resources.
# Author: Tomaž Muraus (http://www.tomaz-muraus.info)
# Version: 1.2.0
# License: GPL

# Requirements:
# - Linux / FreeBSD / Mac OS
# - Python >= 2.5
# - Java (http://www.java.com/en/download/manual.jsp)
# - YUI Compressor (http://developer.yahoo.com/yui/compressor/)
# - Google Closure Compiler (http://code.google.com/closure/compiler/)
# - jpegoptim (http://freshmeat.net/projects/jpegoptim/)
# - optipng (http://optipng.sourceforge.net/)

__version__ = '1.2.0'

import os
import re
import sys
import fcntl
import hashlib
import atexit
import logging
import optparse
import time
import datetime
import shutil
import tempfile
import operator
import subprocess
import threading
import cPickle as pickle

# Path to the external tools / binaries
JAVA_PATH = '/usr/local/bin/java'
YUI_COMPRESSOR_PATH = '/usr/local/bin/yuicompressor.jar'
CLOSURE_COMPILER_PATH = '/usr/local/bin/closure-compiler.jar'
JPEGOPTIM_PATH = '/usr/local/bin/jpegoptim'
OPTIPNG_PATH = '/usr/local/bin/optipng'

class AssetDeflator():
	javascript_re = re.compile(r'<script\s*(?:type=["\']?text/javascript["\']?)?>(.*?)</script>', re.DOTALL | re.IGNORECASE)
	css_re = re.compile(r'<style\s*(?:type=["\']?text/css["\']?)?>(.*?)</style>', re.DOTALL | re.IGNORECASE)
	file_name_suffix = '.min'
	
	event = threading.Event()
	lock = threading.Lock()
	inline_running = False
	inline_run_count = 0
	
	def __init__(self, assets_path, actions, overwrite_original, print_statistics, save_state_file = None, \
				state_file = None, lock_file = '/tmp/asset_deflator.lock'):
		self.assets_path = assets_path
		self.actions = actions
		self.overwrite_original = overwrite_original
		self.print_statistics = print_statistics
		self.save_state_file = save_state_file
		self.state_file = state_file
		
		self.input_files = []
		
		(file_name, file_extension) = os.path.splitext(lock_file)
		self.lock_file = file_name + '.'  + hashlib.md5(self.assets_path).hexdigest() + file_extension

		self.files_count = 0
		self.size_before = {'css': 0, 'js': 0, 'tpl': 0, 'img': 0}
		self.size_after = {'css': 0, 'js': 0, 'tpl': 0, 'img': 0}
		
	def start(self):
		""" Start the minification / compilation / compression process. """
		
		# Only one instance can work on the same path at once
		try:
			self.__lock()
		except IOError:
			print 'Another instance of Asset Deflator is already running - exiting.'
			sys.exit(1)
		
		self.__create_temporary_directories()
		atexit.register(self.__delete_lock_file)
		atexit.register(self.__cleanup_tempporary_files)
		
		actions = {
				'minify_css': {'action': 'minify_css', 'args': None, 'input_files': None},
				'minify_inline_css': {'action': 'compress_inline_code', 'args': 'css', 'input_files': None},
				'compile_js': {'action': 'compile_javascript', 'args': None, 'input_files': None},
				'compile_inline_js': {'action': 'compress_inline_code', 'args': 'js', 'input_files': None},
				'compress_imgs': {'action': 'compress_images', 'args': None, 'input_files': None}
		}
		
		for key in self.actions.keys():
			if key == 'minify_css':
				actions[key]['input_files'] = ()
				actions[key]['input_files'] = self.__find_valid_files(self.assets_path, ['css'])
			elif key == 'minify_inline_css':
				actions[key]['input_files'] = self.__find_files_with_inline_code(self.css_re, self.__find_valid_files(self.assets_path, ['htm', 'html', 'tpl', 'php', 'asp']))
			elif key == 'compile_js':
				actions[key]['input_files'] = self.__find_valid_files(self.assets_path, ['js'])
			elif key == 'compile_inline_js':
				actions[key]['input_files'] = self.__find_files_with_inline_code(self.javascript_re, self.__find_valid_files(self.assets_path, ['htm', 'html', 'tpl', 'php', 'asp']))
			elif key == 'compress_imgs':
				actions[key]['input_files'] = self.__find_valid_files(self.assets_path, ['jpg', 'jpeg', 'png', 'gif'])
		
		if self.state_file:
			# State file is provided, read the file list and skip the files which weren't modified
			files = self.__read_state_file()
			
			if files:
				# If the state file is not empty	
				for key in actions.keys():
					
					input_files = actions[key]['input_files']
					if input_files != None:
						actions[key]['input_files'] = [f for f in input_files if os.path.getmtime(f) != files.get(f, '')]

		self.start_time = time.time()
		workers = []
		for key in self.actions.keys():
			action = getattr(self, actions[key]['action'])
			args = actions[key]['args']
			files = actions[key]['input_files']
			
			if files is not None:
				if args:
					workers.append(threading.Thread(target = action, args = (files, args)))
				else:
					workers.append(threading.Thread(target = action, args = (files,)))
					
		for worker in workers:
			worker.start()
		
		# Wait for all the threads to finish		
		for thread in threading.enumerate():
			if thread is not threading.currentThread():
				thread.join()
		
		self.end_time = time.time()
		
		# If the --save-state option is provided, save the modification dates
		# for all the input files which were modified
		if self.save_state_file:
			
			input_files = [actions[key]['input_files'] for key in actions.keys() \
						if actions[key]['input_files'] != None]

			if input_files:
				input_files = sum(input_files, [])
				input_files = dict([(file, os.path.getmtime(file)) for file in input_files])
				self.__save_state_file(input_files)

		if self.print_statistics:
			self.print_stats()
		
	def minify_css(self, css_files):
		""" Minify CSS files. """
		
		if not css_files:
			return
		
		logging.info('CSS minification: start')
		self.size_before['css'] = self.__calculate_files_size(css_files)
		self.files_count += len(css_files)

		for file in css_files:
			if self.overwrite_original:
				output_file = file
			else:
				output_file = self.__get_file_name_with_suffix(file)
			
			subprocess.Popen('%(java_path)s -jar %(yui_compressor_path)s --type css "%(input_file)s" -o "%(output_file)s"' % {'java_path': JAVA_PATH, 'yui_compressor_path': YUI_COMPRESSOR_PATH, 'input_file': file, 'output_file': output_file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
		
		if self.overwrite_original:
			self.size_after['css'] = self.__calculate_files_size(css_files)
		else:
			self.size_after['css'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, css_files))

		logging.info('CSS minification: completed')
				
	def compile_javascript(self, javascript_files):
		""" Compile JavaScript files with Google Closure Compiler. """
		
		if not javascript_files:
			return
		
		logging.info('JavaScript compilation: start')
		self.size_before['js'] = self.__calculate_files_size(javascript_files)
		
		for file in javascript_files:
			if self.overwrite_original:
				output = subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
			
				with open(file, 'w+') as f:
					f.truncate(0)		
					f.seek(0)
					f.write(output)
			else:
				subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s" --js_output_file "%(output_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file, 'output_file': self.__get_file_name_with_suffix(file)}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
		
		if self.overwrite_original:
			self.size_after['js'] = self.__calculate_files_size(javascript_files)
		else:
			self.size_after['js'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, javascript_files))
		
		logging.info('JavaScript compilation: completed')
		
	def compress_inline_code(self, type, files):
		""" Compress inline CSS or JavaScript code. """
		
		if not files or type not in ['css', 'js']:
			return
		
		# If both compress actions are selected (inline CSS minification and JavaScript compilation), we need to wait for the first
		# one to finish, because they could both work on the same files.
		if 'minify_inline_css' in self.actions and 'compile_inline_js' in self.actions:
			self.lock.acquire()
			if self.inline_running == True:
				self.lock.release()
				self.event.wait()
			else:
				self.lock.release()
				
			self.lock.acquire()
			self.inline_running = True
			self.lock.release()
			
			# If users has selected not to overwrite the original files and this is a second run, we must use already compressed files
			if not self.overwrite_original and self.inline_run_count == 1:
				files = map(self.__get_file_name_with_suffix, files)

		logging.info('Inline %(type)s compression: start' % {'type': 'CSS' if type == 'css' else 'JavaScript'})
		self.size_before['tpl'] = self.__calculate_files_size(files)
		self.files_count += len(files)
		
		# Copy the template files to a temporary directory
		original_file_locations = []
		for index, file in enumerate(files):
			# Index is prepended to the file name, because files can be located in different directories and have the same name
			shutil.copy(file, os.path.join(self.temporaryDirectories[type + '_files'], str(index) + '.' + os.path.basename(file)))
			original_file_locations.append(os.path.dirname(file))
		
		# Find inline code, extract it and write it to a temporary file
		matching_files = {}
		for file in os.listdir(self.temporaryDirectories[type + '_files']):
			with open(os.path.join(self.temporaryDirectories[type + '_files'], file), 'r') as f:
				content = f.read()
				matches = self.css_re.findall(content) if type == 'css' else self.javascript_re.findall(content)
				
				if len(matches) > 0:
					matching_files[file] = []
						
				for match in matches:
					tmp_file = tempfile.NamedTemporaryFile(delete = False)
					tmp_file.write(match)
					tmp_file.close()

					matching_files[file].append(tmp_file.name)
					self.temporaryFiles.append(tmp_file.name)
		
		# Compress the temporary and save the compressed code to a temporary file named <temp_name>.min.extension
		for key, temp_files in matching_files.iteritems():
			for file in temp_files:
				if type == 'css':
					output = subprocess.Popen('%(java_path)s -jar %(yui_compressor_path)s --type css "%(input_file)s"' % {'java_path': JAVA_PATH, 'yui_compressor_path': YUI_COMPRESSOR_PATH, 'input_file': file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
				elif type == 'js':
					output = subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
			 
				with open(file + self.file_name_suffix, 'w+') as f:
					f.write(output)
					  
		# Open the template files with blocks of inline code and replace the non-compressed code with the compressed one
		for index, file in enumerate(os.listdir(self.temporaryDirectories[type + '_files'])):
			
			file_path = os.path.join(self.temporaryDirectories[type + '_files'], file)
			with open(file_path, 'r+') as f_tpl:
				content = f_tpl.read()
				
				for matching_file in matching_files[file]:
					with open(matching_file, 'r') as f:
						original_code = f.read()
						
					with open(matching_file + self.file_name_suffix, 'r') as f:
						compressed_code = f.read()
						
					content = re.sub('%(original_code)s' % {'original_code': re.escape(original_code)}, compressed_code, content, 1)
					
				f_tpl.truncate(0)		
				f_tpl.seek(0)
				f_tpl.write(content)
				
			# Remove index from the file name, (optionally) add a suffix and move file back to the original location
			file_path = self.__remove_index_from_file_name(file_path)
			
			if not self.overwrite_original and self.inline_run_count == 0:
				file_path = self.__add_suffix_after_file_name(file_path)
				
			self.__move_file(file_path, original_file_locations[index])
 
		if self.overwrite_original:
			self.size_after['tpl'] = self.__calculate_files_size(files)
		else:
			self.size_after['tpl'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, files))
		
		if 'minify_inline_css' in self.actions and 'compile_inline_js' in self.actions:  
			# Signal the waiting thread that we are done  
			self.lock.acquire()
			self.inline_running = False
			self.inline_run_count = 1
			self.event.set()
			self.event.clear()
			self.lock.release()

		logging.info('Inline %(type)s compression: completed' % {'type': 'CSS' if type == 'css' else 'JavaScript'})
		
	def compress_images(self, image_files):
		""" Compress images using jpegoptim / optipng tool. """
		
		if not image_files:
			return
		
		logging.info('Image compression: start')
		self.size_before['img'] = self.__calculate_files_size(image_files)
		self.files_count += len(image_files)

		jpg_image_files = filter(lambda file: True if file.split('.')[-1] in ['jpg', 'jpeg'] else False, image_files)
		png_image_files = filter(lambda file: True if file.split('.')[-1] in ['png', 'gif'] else False, image_files)	
		
		if self.overwrite_original:
			destination = ''
		else:
			destination = '--dest="%(temp_directory)s"' % {'temp_directory': self.temporaryDirectories['jpg_files']}

		for file in jpg_image_files:
			subprocess.Popen('%(jpegoptim_path)s --strip-all %(destination)s "%(input_file)s"' % {'jpegoptim_path': JPEGOPTIM_PATH, 'destination': destination, 'input_file': file}, shell = True, stdout = subprocess.PIPE).communicate()[0]
 
			compressed_file_path = os.path.join(self.temporaryDirectories['jpg_files'], os.path.basename(file))
			if not self.overwrite_original and os.path.exists(compressed_file_path):
				# Add a suffix to the file name and move it back to the original location 
				new_name = self.__add_suffix_after_file_name(compressed_file_path)
				self.__move_file(new_name, os.path.dirname(file))
		
		for file in png_image_files:
			if self.overwrite_original:
				output_file = file
			else:
				output_file = self.__get_file_name_with_suffix(file)
				
			subprocess.Popen('%(optipng_path)s "%(input_file)s" -out "%(output_file)s"' % {'optipng_path': OPTIPNG_PATH, 'input_file': file, 'output_file': output_file}, shell = True, stdout = subprocess.PIPE).communicate()[0]
		
		if self.overwrite_original:
			self.size_after['img'] = self.__calculate_files_size(image_files)
		else:
			self.size_after['img'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, image_files))
		
		logging.info('Image compression: completed')
		
	def __calculate_files_size(self, files):
		""" Calculate the size of the files in the list. """
		
		return reduce(operator.add, map(lambda file: os.path.getsize(file) if os.path.exists(file) else 0, files))
		
	def __find_files_with_inline_code(self, regular_expression, files):
		""" Return a list of files which contain text matching the provided regular expression. """
		
		matching_files = []
		for file in files:
			with open(file, 'r') as f:
				content = f.read()
				matches = regular_expression.findall(content)
				
				if len(matches) > 0:
					matching_files.append(file)
					
		return matching_files
	
	def __find_valid_files(self, path, valid_extensions):
		""" Return a list of files with a valid extension. """

		valid_files = []
		for dir_path, dir_name, file_names in os.walk(path):
			for file in file_names:
				extension = file.split('.')[-1]
				
				# Skip the files with .min suffix so we don't do stuff with already minified / compressed files
				if extension in valid_extensions and file.find(self.file_name_suffix) == -1:
					valid_files.append(os.path.join(dir_path, file))

		return valid_files
	
	def __move_file(self, path, destination):
		""" Move a file or multiple files to a destination directory. """

		if os.path.isdir(path):
			for dir_path, dir_names, file_names in os.walk(path):
				for file in file_names:
					file_path = os.path.join(dir_path, file)
					shutil.move(file_path, os.path.join(destination, os.path.basename(file_path)))
		else:
			shutil.move(path, os.path.join(destination, os.path.basename(path)))
	
	def __add_suffix_after_file_name(self, path):
		""" Add a defined suffix after the file name before the file extension. """
		
		if os.path.isdir(path):
			for dir_path, dir_names, files in os.walk(path):
				for file in files:
					file_path = os.path.join(dir_path, file)  
					
					new_file_name = self.__get_file_name_with_suffix(file_path)
					shutil.move(file_path, new_file_name)
		else:
			new_file_name = self.__get_file_name_with_suffix(path)	  
			shutil.move(path, new_file_name)
			
			return new_file_name
		
	def __remove_index_from_file_name(self, path):
		""" Remove index from the beginning of the file name. """
		
		new_file_name = path[path.find('.') + 1:]
		shutil.move(path, new_file_name)	
		
		return new_file_name
			
	def __get_file_name_with_suffix(self, file_name):
		""" Return file name with added suffix. """
		
		(name, extension) = os.path.splitext(file_name)
		new_name = name + self.file_name_suffix + extension
			
		return new_name 
	
	def __create_temporary_directories(self):
		""" Create temporary directories. """
		
		self.temporaryDirectories = {}
		self.temporaryFiles = []
		
		self.temporaryDirectories['css_files'] = tempfile.mkdtemp()
		self.temporaryDirectories['js_files'] = tempfile.mkdtemp()
		self.temporaryDirectories['jpg_files'] = tempfile.mkdtemp()
		self.temporaryDirectories['png_files'] = tempfile.mkdtemp()
		
	def __cleanup_tempporary_files(self):
		""" Delete all temporary directories and files. """
		
		for key, directory in self.temporaryDirectories.iteritems():
			if os.path.exists(directory):
				shutil.rmtree(directory, ignore_errors = True)
			
		for file in self.temporaryFiles:
			if os.path.exists(file):
				os.remove(file)
				
	def __lock(self):
		""" Create a lock file. """

		self.lockfp = open(self.lock_file, 'w')
		fcntl.lockf(self.lockfp, fcntl.LOCK_EX | fcntl.LOCK_NB)
		
	def __delete_lock_file(self):
		""" Delete a lock file. """
		
		if os.path.exists(self.lock_file):
			os.unlink(self.lock_file)
			
	def __read_state_file(self):
		"""
		Reads file paths and modification times from the state file
		and returns a dictionary (file_path: modification_time) on
		success, None otherwise.
		"""
		
		try:
			with open(self.state_file, 'r') as file:
				return pickle.load(file)
		except Exception, e:
			return None

	def __save_state_file(self, files = None):
		""" Saves the state to a file. """
		
		current_state = self.__read_state_file()
		with open(self.save_state_file, 'w+') as file:
			if current_state:
				# If previous state is available, update it
				current_state.update(files)
				new_state = current_state
			else:
				new_state = files	

			pickle.dump(new_state, file)
	
	def print_stats(self):
		print 'Statistics'
		print ''
		
		if self.files_count == 0:
			print 'Found 0 files to work on in %(assets_path)s'  % {'assets_path': self.assets_path}
		else:
			print 'Performed work on %(files_count)d files located in %(assets_path)s'  % {'files_count': self.files_count, 'assets_path': self.assets_path}
			
		print 'Running time: %(running_time)s' % {'running_time': str(datetime.timedelta(seconds = int(self.end_time - self.start_time)))}
		
		if 'minify_css' in self.actions and self.size_before['css'] > 0:
			print ''
			print 'CSS files:'
			print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['css'], 'size_after': self.size_after['css'], 'difference': operator.neg((100 - ((float(self.size_after['css']) / self.size_before['css']) * 100)))}
			
		if 'compile_js' in self.actions and self.size_before['js'] > 0:
			print ''
			print 'JavaScript files:'
			print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['js'], 'size_after': self.size_after['js'], 'difference': operator.neg((100 - (float(self.size_after['js']) / self.size_before['js'] * 100)))}
			
		if ('compile_inline_js' in self.actions or 'minify_inline_css' in self.actions) and self.size_before['tpl'] > 0:
			print ''
			print 'Templates:'
			print 'Size before: %(size_before)d bytes, Size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['tpl'], 'size_after': self.size_after['tpl'], 'difference': operator.neg((100 - (float(self.size_after['tpl']) / self.size_before['tpl'] * 100)))}
			
		if 'compress_imgs' in self.actions and self.size_before['img'] > 0:
			print ''
			print 'Image files:'
			print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['img'], 'size_after': self.size_after['img'], 'difference': operator.neg((100 - (float(self.size_after['img']) / self.size_before['img'] * 100)))}

		if self.files_count > 0:
			print ''
			print 'Total:'
			print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': reduce(operator.add, self.size_before.values()), 'size_after': reduce(operator.add, self.size_after.values()), 'difference': operator.neg((100 - (float(reduce(operator.add, self.size_after.values())) / reduce(operator.add, self.size_before.values()) * 100)))}

if __name__ == '__main__':	
	parser = optparse.OptionParser(version = '%prog ' + __version__)
	parser.add_option('-v', '--verbose', action = 'store_true', default = False, dest = 'verbose', help = 'print what is going on [default: %default]')
	parser.add_option('-o', '--overwrite', action = 'store_true', default = False, dest = 'overwrite_original', help = 'overwrite the original files (don\'t create new files with .min extension) [default: %default]')
	parser.add_option('-s', '--statistics', action = 'store_true', default = False, dest = 'print_statistics', help = 'print statistics at the end')
	
	parser.add_option('--save-state', action = 'store', type = 'string', dest = 'save_state', metavar = 'STATE_FILE', help = 'save the name and the last modified time of the files which were accessed during this run')
	parser.add_option('--skip-not-modified', action = 'store', type = 'string', dest = 'skip_not_modified', metavar = 'STATE_FILE', help = 'skip the files located in the provided state file which weren\'t modified since the last run')
	
	parser.add_option('--path', action = 'store', type = 'string', dest = 'assets_path', metavar = 'PATH', help = 'path to your asset files')
	parser.add_option('--all', action = 'store_true', default = False, dest = 'run_all', help = 'run all actions') 
	parser.add_option('--minify-css', action = 'store_true', default = False, dest = 'action_minify_css', help = 'minify the CSS files')
	parser.add_option('--minify-inline-css', action = 'store_true', default = False, dest = 'action_minify_inline_css', help = 'find and minify blocks of CSS in your templates')
	parser.add_option('--compile-js', action = 'store_true', default = False, dest = 'action_compile_js', help = 'compile JavaScript files using Google Closure Compiler')
	parser.add_option('--compile-inline-js', action = 'store_true', default = False, dest = 'action_compile_inline_js', help = 'find and compile blocks of JavaScript in your templates using Google Closure Compiler')
	parser.add_option('--compress-images', action = 'store_true', default = False, dest = 'action_compress_imgs', help = 'compress image files')
	
	(options, args) = parser.parse_args()
	options = vars(options)

	if not options['assets_path']:
		parser.error('you must supply location of your assets')
		
	if options['run_all']:
		actions = dict([(key[key.find('_') + 1:], True) for key, value in options.iteritems() if key.find('action_') != -1])
	else:
		actions = dict([(key[key.find('_') + 1:], value) for key, value in options.iteritems() if key.find('action_') != -1 and value != False])
	
	if not actions:
		parser.error('you must supply at least one action')
	
	# Set up logging	
	logging.basicConfig(level = logging.INFO if options['verbose'] else logging.ERROR, format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt = '%d.%m.%Y %H:%M:%S')

	asset_deflator = AssetDeflator(options['assets_path'], actions, options['overwrite_original'], options['print_statistics'], \
								options['save_state'], options['skip_not_modified'])
	asset_deflator.start()